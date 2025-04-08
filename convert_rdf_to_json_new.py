import rdflib
from rdflib import Graph, URIRef, BNode, Literal
from rdflib.namespace import RDF, RDFS, OWL, XSD
from collections import defaultdict
import os
import json

# --- Helper to extract local name from URI ---
def get_local_name(uri_ref):
    uri_str = str(uri_ref)
    if '#' in uri_str:
        return uri_str.split('#')[-1]
    elif '/' in uri_str:
        return uri_str.split('/')[-1]
    else:
        return uri_str

# --- Helper to map RDF data types to Neo4j data types ---
def map_rdf_to_neo4j_datatype(rdf_datatype):
    if rdf_datatype == XSD.string:
        return "String"
    elif rdf_datatype == XSD.integer:
        return "Integer"
    elif rdf_datatype == XSD.float or rdf_datatype == XSD.double or rdf_datatype == XSD.decimal:
        return "Float"
    elif rdf_datatype == XSD.boolean:
        return "Boolean"
    elif rdf_datatype == XSD.date:
        return "Date"
    elif rdf_datatype == XSD.dateTime:
        return "DateTime"
    else:
        return "String"  # Default to String if type is unknown

# --- Main function to extract ontology and map to Neo4j-style format ---
def extract_for_neo4j(ttl_file_path):
    if not os.path.exists(ttl_file_path):
        print(f"Error: File not found at {ttl_file_path}")
        return None

    g = Graph()
    print(f"Attempting to parse {ttl_file_path}...")
    try:
        g.parse(ttl_file_path, format="turtle")
        print("Parsing successful.")
    except Exception as e:
        print(f"Error parsing TTL file: {e}")
        return None

    # --- Identify all classes ---
    all_classes = set()
    for s, p, o in g.triples((None, RDF.type, OWL.Class)):
        if isinstance(s, URIRef):
            all_classes.add(s)
    for s, p, o in g.triples((None, RDF.type, RDFS.Class)):
        if isinstance(s, URIRef):
            all_classes.add(s)
    for s, p, o in g.triples((None, RDFS.subClassOf, None)):
        if isinstance(s, URIRef):
            all_classes.add(s)
        if isinstance(o, URIRef) and o not in [OWL.Thing, RDFS.Resource]:
            all_classes.add(o)

    user_classes = {c for c in all_classes if c not in [OWL.Thing, RDFS.Resource]}
    print(f"Identified {len(user_classes)} user-defined classes.")

    # --- Identify object and data properties ---
    object_properties = set()
    for s, p, o in g.triples((None, RDF.type, OWL.ObjectProperty)):
        object_properties.add(s)

    data_properties = set()
    for s, p, o in g.triples((None, RDF.type, OWL.DatatypeProperty)):
        data_properties.add(s)

    print(f"Identified {len(object_properties)} object properties (Neo4j relationships).")
    print(f"Identified {len(data_properties)} data properties (Neo4j node properties).")

    # --- Build node structure ---
    neo4j_nodes = defaultdict(lambda: {
        "inherits_from": set(),
        "properties": {}  # Changed to a dictionary to store data types
    })

    for cls_uri in user_classes:
        cls_name = get_local_name(cls_uri)

        # Subclass (inheritance)
        for parent_uri in g.objects(subject=cls_uri, predicate=RDFS.subClassOf):
            if isinstance(parent_uri, URIRef) and parent_uri not in [OWL.Thing, RDFS.Resource]:
                neo4j_nodes[cls_name]["inherits_from"].add(get_local_name(parent_uri))

        # Data properties where class is domain
        for prop_uri in g.subjects(predicate=RDFS.domain, object=cls_uri):
            if prop_uri in data_properties:
                prop_name = get_local_name(prop_uri)
                # Determine data type
                range_uri = g.value(subject=prop_uri, predicate=RDFS.range)
                if range_uri:
                    neo4j_datatype = map_rdf_to_neo4j_datatype(range_uri)
                    neo4j_nodes[cls_name]["properties"][prop_name] = neo4j_datatype
                else:
                    neo4j_nodes[cls_name]["properties"][prop_name] = "String" # Default to String

    # --- Build relationship structure ---
    neo4j_relationships = {}
    for op_uri in object_properties:
        op_name = get_local_name(op_uri)

        # Get domain(s) = Start node
        domains = set()
        for d in g.objects(subject=op_uri, predicate=RDFS.domain):
            if isinstance(d, URIRef):
                domains.add(get_local_name(d))

        # Get range(s) = End node
        ranges = set()
        for r in g.objects(subject=op_uri, predicate=RDFS.range):
            if isinstance(r, URIRef):
                ranges.add(get_local_name(r))

        neo4j_relationships[op_name] = {
            "start_node_labels": sorted(list(domains)) if domains else None,
            "end_node_labels": sorted(list(ranges)) if ranges else None
        }

    # --- Convert sets to lists and finalize property structure ---
    final_nodes = {}
    for node, data in neo4j_nodes.items():
        final_nodes[node] = {
            "inherits_from": sorted(list(data["inherits_from"])),
            "properties": data["properties"]  # Keep the dictionary structure
        }

    return {
        "nodes": final_nodes,
        "relationships": neo4j_relationships
    }

# --- Main block ---
if __name__ == "__main__":
    # === EDIT BELOW ===
    TTL_FILE_PATH = "new_RetailOntologyv4.ttl"
    OUTPUT_JSON_FILE = "neo4j_ontology_output_new.json"
    # ===================

    print(f"Using input: {TTL_FILE_PATH}")
    print(f"Saving Neo4j-style JSON to: {OUTPUT_JSON_FILE}")

    extracted = extract_for_neo4j(TTL_FILE_PATH)

    if extracted:
        try:
            with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f:
                json.dump(extracted, f, indent=2, ensure_ascii=False)
            print(f"✅ Output saved to {OUTPUT_JSON_FILE}")
        except Exception as e:
            print(f"❌ Error writing file: {e}")
    else:
        print("❌ No data extracted.")
