import json
import os
from neo4j import GraphDatabase, time
from datetime import datetime, timedelta, timezone

# === CONFIGURATION ===
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "Arjun#1234"  # Change if needed
NEO4J_DATABASE = "retailsales"  # Add the database name here

# === CORRECT FILE PATH ===
# Get the directory of the current script
script_dir = os.path.dirname(os.path.abspath(__file__))
# Construct the absolute path to the JSON file
ONTOLOGY_JSON = os.path.join(script_dir, "neo4j_ontology_output_new.json")

# === CONNECT TO NEO4J ===
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# === LOAD JSON ===
with open(ONTOLOGY_JSON, "r", encoding="utf-8") as f:
    data = json.load(f)

nodes = data["nodes"]
relationships = data["relationships"]

# === HELPER: Generate realistic sample values based on data type ===
def get_realistic_value(prop_name, data_type):
    prop_name_lower = prop_name.lower()
    if data_type == "Date":
        # Generate a date within the last year
        today = datetime.today()
        random_days = (hash(prop_name) % 365)  # Use hash to get a pseudo-random number
        random_date = today - timedelta(days=random_days)
        return random_date.date()  # Return a Python date object
    elif data_type == "DateTime":
        # Generate a datetime within the last year
        today = datetime.now(timezone.utc)  # Use timezone-aware datetime
        random_days = (hash(prop_name) % 365)  # Use hash to get a pseudo-random number
        random_time = timedelta(hours=(hash(prop_name) % 24), minutes=(hash(prop_name) % 60))
        random_datetime = today - timedelta(days=random_days) + random_time
        return random_datetime  # Return a Python datetime object
    elif data_type == "Integer":
        return hash(prop_name) % 1000  # Generate a pseudo-random integer
    elif data_type == "Float":
        return (hash(prop_name) % 1000) / 10.0  # Generate a pseudo-random float
    elif data_type == "Boolean":
        return bool(hash(prop_name) % 2)  # Generate a pseudo-random boolean
    elif data_type == "String":
        return f"sample_value_{hash(prop_name) % 100}"  # Default string value
    else:
        return f"unknown_type_{hash(prop_name) % 100}"

# === CREATE NODE FUNCTION ===
def create_node(tx, label, properties):
    query = f"""
    MERGE (n:{label} {{name: $label}})
    SET n += $props
    """
    props = {}
    for prop, data_type in properties.items():
        props[prop] = get_realistic_value(prop, data_type)
    tx.run(query, label=label, props=props)

# === CREATE INHERITANCE RELATIONSHIP ===
def create_inheritance(tx, child, parent):
    query = """
    MATCH (c {name: $child}), (p {name: $parent})
    MERGE (c)-[:IS_A]->(p)
    """
    tx.run(query, child=child, parent=parent)

# === CREATE DOMAIN-RANGE RELATIONSHIP ===
def create_relationship(tx, rel_type, starts, ends):
    if not starts or not ends:
        return
    for start in starts:
        for end in ends:
            query = f"""
            MATCH (a {{name: $start}}), (b {{name: $end}})
            MERGE (a)-[r:{rel_type}]->(b)
            """
            tx.run(query, start=start, end=end)

# === MAIN EXECUTION ===
with driver.session(database=NEO4J_DATABASE) as session:
    print("Creating nodes...")
    for node_label, details in nodes.items():
        session.execute_write(create_node, node_label, details.get("properties", {}))

    print("Creating inheritance relationships...")
    for child, details in nodes.items():
        for parent in details.get("inherits_from", []):
            session.execute_write(create_inheritance, child, parent)

    print("Creating object property relationships...")
    for rel_type, rel_data in relationships.items():
        session.execute_write(
            create_relationship,
            rel_type,
            rel_data.get("start_node_labels"),
            rel_data.get("end_node_labels")
        )

driver.close()
print("✅ Import complete.")
