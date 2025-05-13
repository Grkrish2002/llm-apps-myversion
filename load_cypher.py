import os
from neo4j import GraphDatabase
import logging
import re # Import the 're' module for regular expressions
import json # Import the json module

# --- Configuration ---
NEO4J_URI = "neo4j://localhost:7687"  # Replace with your Neo4j URI
NEO4J_USER = "neo4j"         # Replace with your Neo4j username
NEO4J_PASSWORD = "Arjun#1234"     # Replace with your Neo4j password
NEO4J_DATABASE = "apparelsales0501"             # Replace with your target database name

# Determine the directory of the current script
try:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # Fallback for environments where __file__ is not defined (e.g., interactive interpreters)
    SCRIPT_DIR = os.getcwd()

CYPHER_FILE_PATH = os.path.join(SCRIPT_DIR, "generated_data.cypher") # Explicitly set absolute path

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def execute_cypher_file(driver, filepath, database):
    """Reads a Cypher file, cleans comments, and executes its statements transactionally."""
    if not os.path.exists(filepath):
        logging.error(f"Cypher file not found: {filepath}")
        return

    logging.info(f"Reading Cypher file: {filepath}")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            # Read the whole file
            full_cypher_script = f.read()

    except Exception as e:
        logging.error(f"Error reading Cypher file {filepath}: {e}")
        return

    # Clean the entire script: remove comment lines
    cleaned_lines = []
    for line in full_cypher_script.splitlines():
        stripped_line = line.strip()
        if stripped_line and not stripped_line.startswith('//'):
            cleaned_lines.append(stripped_line)

    logging.info(f"Processing Cypher statements from {filepath} against database '{database}'...")

    # Use try-with-resources for session management
    try:
        with driver.session(database=database) as session:
            current_parameters = {} # To store parsed :param values
            statement_buffer = []   # To accumulate multi-line statements (UNWIND blocks)
            active_param_name_for_block = None # Tracks the $param for the current UNWIND block
            count = 0

            def execute_buffer(s_buffer, params_for_run):
                nonlocal count
                if not s_buffer:
                    return
                full_query = "\n".join(s_buffer).strip()
                if full_query:
                    logging.debug(f"Executing block: {full_query[:200]}... with params: {list(params_for_run.keys()) if params_for_run else 'None'}")
                    session.execute_write(lambda tx: tx.run(full_query, parameters=params_for_run))
                    count +=1
                    logging.info(f"  Executed statement block. Total blocks/statements executed: {count}")
                s_buffer.clear()

            for line_num, raw_line in enumerate(cleaned_lines):
                line = raw_line.strip().rstrip(';') # Remove trailing semicolons for consistency

                if not line: continue

                is_param_def = line.upper().startswith(":PARAM")
                is_unwind_with_param = line.upper().startswith("UNWIND $")
                is_constraint_or_index = "CONSTRAINT IF NOT EXISTS" in line.upper() or \
                                         "INDEX IF NOT EXISTS" in line.upper() or \
                                         line.upper().startswith("CREATE CONSTRAINT") or \
                                         line.upper().startswith("CREATE INDEX")

                # If we hit a new :PARAM or a new UNWIND $ or a constraint/index,
                # and the buffer has content, execute the buffered content.
                if (is_param_def or is_unwind_with_param or is_constraint_or_index) and statement_buffer:
                    params_to_use = {active_param_name_for_block: current_parameters[active_param_name_for_block]} \
                                    if active_param_name_for_block and active_param_name_for_block in current_parameters else {}
                    execute_buffer(statement_buffer, params_to_use)
                    active_param_name_for_block = None # Reset for the new block

                if is_param_def:
                    match = re.match(r":param\s+([a-zA-Z0-9_]+)\s*=>\s*(.+)", line, re.IGNORECASE)
                    if match:
                        param_name = match.group(1)
                        json_data_str = match.group(2)
                        try:
                            current_parameters[param_name] = json.loads(json_data_str)
                            logging.info(f"  Parsed and stored parameter: {param_name}")
                        except json.JSONDecodeError as je:
                            logging.error(f"Error decoding JSON for parameter '{param_name}' on line {line_num+1}: {je}")
                            logging.error(f"Problematic JSON string: {json_data_str[:200]}...")
                            raise # Stop execution
                    else:
                        logging.warning(f"Could not parse :param line {line_num+1}: {line[:100]}...")
                elif is_unwind_with_param:
                    unwind_match = re.match(r"UNWIND\s+\$([a-zA-Z0-9_]+)", line, re.IGNORECASE)
                    if unwind_match:
                        active_param_name_for_block = unwind_match.group(1)
                        if active_param_name_for_block not in current_parameters:
                            logging.error(f"UNWIND statement on line {line_num+1} uses unknown parameter '${active_param_name_for_block}'. Known: {list(current_parameters.keys())}")
                            active_param_name_for_block = None # Invalidate
                    statement_buffer.append(line) # Add the UNWIND line
                elif is_constraint_or_index:
                    # Execute constraints/indexes immediately as they are standalone
                    execute_buffer([line], {}) # Pass empty params
                elif line: # Any other line, could be MERGE, SET, etc.
                    statement_buffer.append(line)
                    # If this line ends a typical UNWIND block (e.g., a SET statement)
                    # and an active parameter was set, we might execute.
                    # However, the generated script puts each part (UNWIND, MERGE, SET) on new lines
                    # and `write_cypher` adds a semicolon. The logic above to execute buffer
                    # when a new UNWIND or :PARAM is hit should handle block endings.
                    # For the very last block in the file, it will be handled after the loop.

            # After the loop, execute any remaining statements in the buffer
            if statement_buffer:
                params_to_use = {active_param_name_for_block: current_parameters[active_param_name_for_block]} \
                                if active_param_name_for_block and active_param_name_for_block in current_parameters else {}
                execute_buffer(statement_buffer, params_to_use)

            logging.info(f"Successfully processed Cypher file. Total logical blocks/statements executed: {count}.")

    except Exception as e:
        logging.error(f"Failed to execute Cypher script: {e}")


if __name__ == "__main__":
    logging.info("Connecting to Neo4j...")
    try:
        # Establish the driver connection
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity() # Check if connection is successful
        logging.info("Connection successful.")

        # Execute the script
        execute_cypher_file(driver, CYPHER_FILE_PATH, NEO4J_DATABASE)

        # Close the driver connection
        driver.close()
        logging.info("Driver connection closed.")

    except Exception as e:
        logging.error(f"Failed to connect or execute: {e}")
