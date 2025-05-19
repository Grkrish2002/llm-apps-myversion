import json
import os
from collections import defaultdict
import logging
import html  # For escaping HTML characters

# --- Configuration ---
# Use schema_analysis.json as it contains the structured node/relationship data
SCHEMA_FILENAME = "schema_analysis.json"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_HTML_FILENAME = "neighborhood_analysis_report.html" # Output filename
SCHEMA_FILE_PATH = os.path.join(SCRIPT_DIR, SCHEMA_FILENAME)

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Functions ---

def load_schema(file_path):
    """Loads the schema analysis JSON file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        logger.info(f"Successfully loaded schema from {file_path}")
        return schema
    except FileNotFoundError:
        logger.error(f"Schema file not found: {file_path}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from schema file: {file_path} - {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred loading {file_path}: {e}")
        return None

def analyze_connectivity(schema_nodes, schema_relationships):
    """Analyzes schema connectivity for immediate neighbors."""
    nodes = list(schema_nodes.keys())
    outgoing_neighbors = defaultdict(list) # node -> [(rel_type, target_node)]
    incoming_neighbors = defaultdict(list) # node -> [(rel_type, source_node)]

    if not isinstance(schema_relationships, list):
        logger.warning("Relationships data is not a list in the schema. Cannot analyze connectivity.")
        return nodes, {}, {} # Return empty structures for neighbors

    for rel in schema_relationships:
        if not isinstance(rel, dict):
            logger.warning(f"Skipping invalid relationship entry (not a dictionary): {rel}")
            continue

        source = rel.get('source')
        target = rel.get('target')
        rel_type = rel.get('type')

        if source and target and rel_type and isinstance(source, str) and isinstance(target, str):
            # Store neighbors
            outgoing_neighbors[source].append((rel_type, target))
            incoming_neighbors[target].append((rel_type, source))
        else:
            logger.warning(f"Skipping invalid relationship definition (missing/invalid source, target, or type): {rel}")

    # Return only necessary data for neighbor analysis
    return nodes, outgoing_neighbors, incoming_neighbors

def generate_html_report(nodes, outgoing_neighbors, incoming_neighbors, schema_nodes_data):
    """Generates an HTML string report focusing on immediate neighbors and properties."""
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Neighborhood Analysis Report</title>
        <style>
            body { font-family: sans-serif; line-height: 1.6; padding: 20px; background-color: #f8f9fa; color: #212529; }
            .container { max-width: 1200px; margin: auto; background: #fff; padding: 25px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #0056b3; text-align: center; border-bottom: 2px solid #0056b3; padding-bottom: 10px; margin-bottom: 30px; }
            h2 { color: #0056b3; margin-top: 40px; border-bottom: 1px solid #dee2e6; padding-bottom: 8px; }
            h3 { color: #495057; margin-top: 25px; font-weight: bold; }
            .node-properties { font-weight: normal; font-size: 0.9em; color: #6c757d; margin-left: 10px; }
            code { background-color: #e2e6ea; padding: 2px 5px; border-radius: 3px; font-family: monospace; }
            ul { list-style-type: none; padding-left: 0; }
            li { background-color: #e9ecef; margin-bottom: 6px; padding: 8px; border-radius: 4px; border-left: 4px solid #007bff; }
            li.incoming { border-left-color: #28a745; }
            .neighbor-section { margin-left: 15px; }
            .node-analysis { margin-bottom: 30px; padding: 15px; border: 1px solid #ced4da; border-radius: 5px; background-color: #fdfdff;}
            p.no-results { color: #6c757d; font-style: italic; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Schema Connectivity Report</h1>
    """

    # Immediate Neighbors Analysis
    html_content += "<h2>Immediate Neighbors Analysis</h2>\n"
    for node in sorted(nodes): # Sort alphabetically for this section
        # Get properties for the analyzed node
        node_props_list = []
        node_data = schema_nodes_data.get(node, {})
        # Handle both list-of-dicts and dict-of-strings/dicts property formats
        props_structure = node_data.get("properties", [])
        if isinstance(props_structure, list): # Expected format from schema_analyzer
            node_props_list = [p.get("name", "?") for p in props_structure if isinstance(p, dict)]
        elif isinstance(props_structure, dict): # Handle older format if necessary
            node_props_list = list(props_structure.keys())

        node_props_str = ", ".join(f"<code>{html.escape(p)}</code>" for p in sorted(node_props_list))
        node_props_display = f"({node_props_str})" if node_props_str else ""

        html_content += f"<div class='node-analysis'><h3>Node: <code>{html.escape(node)}</code> <span class='node-properties'>{node_props_display}</span></h3>\n"

        # Outgoing
        html_content += "<div class='neighbor-section'><h4>Outgoing Neighbors:</h4>\n"
        if node in outgoing_neighbors:
            html_content += "<ul>\n"
            for rel_type, target_node in sorted(outgoing_neighbors[node]):
                # Get properties for the target node
                target_props_list = []
                target_node_data = schema_nodes_data.get(target_node, {})
                target_props_structure = target_node_data.get("properties", [])
                if isinstance(target_props_structure, list):
                    target_props_list = [p.get("name", "?") for p in target_props_structure if isinstance(p, dict)]
                elif isinstance(target_props_structure, dict):
                    target_props_list = list(target_props_structure.keys())

                target_props_str = ", ".join(f"<code>{html.escape(p)}</code>" for p in sorted(target_props_list))
                target_props_display = f"({target_props_str})" if target_props_str else ""
                html_content += f"    <li>-&gt; [<strong>{html.escape(rel_type)}</strong>] -&gt; <code>{html.escape(target_node)}</code> <span class='node-properties'>{target_props_display}</span></li>\n"
            html_content += "</ul></div>\n"
        else:
            html_content += "<p class='no-results'>None</p></div>\n"

        # Incoming
        html_content += "<div class='neighbor-section'><h4>Incoming Neighbors:</h4>\n"
        if node in incoming_neighbors:
            html_content += "<ul>\n"
            for rel_type, source_node in sorted(incoming_neighbors[node]):
                # Get properties for the source node
                source_props_list = []
                source_node_data = schema_nodes_data.get(source_node, {})
                source_props_structure = source_node_data.get("properties", [])
                if isinstance(source_props_structure, list):
                    source_props_list = [p.get("name", "?") for p in source_props_structure if isinstance(p, dict)]
                elif isinstance(source_props_structure, dict):
                    source_props_list = list(source_props_structure.keys())

                source_props_str = ", ".join(f"<code>{html.escape(p)}</code>" for p in sorted(source_props_list))
                source_props_display = f"({source_props_str})" if source_props_str else ""
                html_content += f"    <li class=\"incoming\">&lt;- [<strong>{html.escape(rel_type)}</strong>] &lt;- <code>{html.escape(source_node)}</code> <span class='node-properties'>{source_props_display}</span></li>\n"
            html_content += "</ul></div>\n"
        else:
            html_content += "<p class='no-results'>None</p></div>\n"

        html_content += "</div>\n" # Close node-analysis div

    html_content += """
        </div>
    </body>
    </html>
    """
    return html_content

# --- Main Execution ---
if __name__ == "__main__":
    logger.info(f"Loading schema from: {SCHEMA_FILE_PATH}")
    schema = load_schema(SCHEMA_FILE_PATH)
    if not schema:
        print(f"Error: Could not load schema file {SCHEMA_FILENAME}. Exiting.")
        exit(1)

    # Extract nodes and relationships safely
    nodes_data = schema.get('nodes', {})
    relationships_data = schema.get('relationships', [])

    if not isinstance(nodes_data, dict):
        logger.error("Schema format error: 'nodes' section is not a dictionary.")
        print("Error: Invalid schema format in 'nodes' section. Exiting.")
        exit(1)
    if not isinstance(relationships_data, list):
         logger.error("Schema format error: 'relationships' section is not a list.")
         print("Error: Invalid schema format in 'relationships' section. Exiting.")
         exit(1)

    logger.info("Analyzing schema connectivity...")
    nodes, out_neighbors, in_neighbors = analyze_connectivity(nodes_data, relationships_data) # Updated return values
    logger.info("Connectivity analysis complete.")

    # Generate HTML report
    logger.info("Generating HTML report...")
    html_report = generate_html_report(nodes, out_neighbors, in_neighbors, nodes_data) # Pass nodes_data for property lookup

    # Write HTML report to file
    output_filepath = os.path.join(SCRIPT_DIR, OUTPUT_HTML_FILENAME)
    try:
        with open(output_filepath, 'w', encoding='utf-8') as f:
            f.write(html_report)
        logger.info(f"Successfully wrote HTML report to {output_filepath}")
        print(f"\nReport generated: {output_filepath}")
    except IOError as e:
        logger.error(f"Could not write HTML report to {output_filepath}: {e}")
        print(f"\nError writing report: {e}")
    except Exception as e:
         logger.error(f"An unexpected error occurred during HTML report writing: {e}", exc_info=True)
         print(f"\nUnexpected error writing report: {e}")