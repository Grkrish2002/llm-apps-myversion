import json
import os

# --- Configuration ---
# Use an absolute path for clarity, adjust if needed
SCHEMA_FILE_PATH = r"c:\Krishna\VD_PROJECTS\VD_PROJECTS\Neo4j\agent_outputs0501\schema_analysis.json"
# --- End Configuration ---

def print_nodes_with_multiple_properties(filepath):
    """
    Reads a schema JSON file and prints node labels with more than one property
    in the specified format.

    Args:
        filepath (str): The absolute path to the schema JSON file.
    """
    if not os.path.exists(filepath):
        print(f"Error: Schema file not found at {filepath}")
        return

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            schema_data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from {filepath}: {e}")
        return
    except Exception as e:
        print(f"An unexpected error occurred while reading {filepath}: {e}")
        return

    nodes_schema = schema_data.get("nodes")

    if not isinstance(nodes_schema, dict):
        print("Error: 'nodes' key not found or is not a dictionary in the schema file.")
        return

    print("Node labels with more than one property:")
    print("-" * 40)

    for label, details in nodes_schema.items():
        properties = details.get("properties")

        if isinstance(properties, list) and len(properties) > 1:
            property_strings = []
            for prop in properties:
                prop_name = prop.get("name")
                prop_type = prop.get("type")
                if prop_name and prop_type:
                    property_strings.append(f"{prop_name} ({prop_type})")

            if property_strings: # Only print if we successfully formatted properties
                print(f"{label}: {', '.join(property_strings)}")

# --- Main Execution ---
if __name__ == "__main__":
    print_nodes_with_multiple_properties(SCHEMA_FILE_PATH)
