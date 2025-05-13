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

    # Clean the entire script: remove comment lines and ensure statements are separated correctly
    cleaned_lines = []
    for line in full_cypher_script.splitlines():
        stripped_line = line.strip()
        if stripped_line and not stripped_line.startswith('//'):
            cleaned_lines.append(stripped_line)

    # Reconstruct the script content from cleaned lines
    full_script_content = "\n".join(cleaned_lines)

    # Split the script into individual statements.
    # A common pattern in the generated script is ';<newline><newline>' or just ';' followed by UNWIND/MATCH/CREATE.
    # Splitting by ';' and filtering empty strings is a reasonable approach here.
    # --- Improved statement splitting ---
    potential_statements = [stmt.strip() for stmt in full_script_content.split(';') if stmt.strip()]
    valid_statements = []
    # Keywords that typically start a valid, executable statement in our generated file
    # Note: Case-insensitive check will be used.
    valid_start_keywords = ("UNWIND", "MATCH", "CREATE", "MERGE")

    for stmt in potential_statements:
        # Find the first non-comment line in the statement block
        first_code_line = ""
        for line in stmt.splitlines():
            stripped_line = line.strip()
            if stripped_line and not stripped_line.startswith('//'):
                first_code_line = stripped_line
                break # Found the first non-comment line

        # Check if the first *code* line starts with a known Cypher keyword (case-insensitive).
        # This helps filter out fragments potentially caused by semicolons within string literals in the JSON.
        # Also check for INDEX/CONSTRAINT creation which might not start with the keywords above.
        if first_code_line and (
            first_code_line.upper().startswith(valid_start_keywords) or
            "INDEX IF NOT EXISTS" in first_code_line.upper() or
            "CONSTRAINT IF NOT EXISTS" in first_code_line.upper()):
            valid_statements.append(stmt)
        else:
            logging.debug(f"Skipping potential statement fragment (first code line: '{first_code_line[:50]}...'): {stmt[:100]}...") # Log skipped parts for debugging

    if not valid_statements:
        logging.warning(f"No valid Cypher statements found after filtering in {filepath}")
        return

    logging.info(f"Executing {len(valid_statements)} filtered Cypher statements from {filepath} against database '{database}'...")

    # Use try-with-resources for session management
    try:
        with driver.session(database=database) as session:
            # Execute each statement in its own transaction
            count = 0
            total = len(valid_statements) # Use the filtered list here
            for stmt in valid_statements: # Iterate over the filtered list
                try:
                    # Check if it's an UNWIND statement to parameterize it
                    if stmt.strip().upper().startswith("UNWIND"):
                        try:
                            # Find the end of the first line (UNWIND ... AS row)
                            first_line_end = stmt.find('\n')
                            if first_line_end == -1: first_line_end = len(stmt)
                            first_line = stmt[:first_line_end].strip()
                            rest_of_stmt = stmt[first_line_end:].strip()

                            # Extract JSON list string between "UNWIND " and " AS "
                            # Assumes variable name is 'row' as generated by the script
                            json_match = re.search(r"UNWIND\s+(.+)\s+AS\s+row", first_line, re.IGNORECASE | re.DOTALL)
                            if not json_match:
                                raise ValueError("Could not extract JSON part from UNWIND statement.")

                            json_list_str = json_match.group(1).strip()

                            # Parse the JSON string into a Python list
                            parsed_data = json.loads(json_list_str)

                            # Create the parameterized statement
                            parameterized_stmt = f"UNWIND $rows AS row\n{rest_of_stmt}"

                            # Execute with parameters
                            session.execute_write(lambda tx: tx.run(parameterized_stmt, rows=parsed_data))

                        except (json.JSONDecodeError, ValueError, IndexError, AttributeError) as parse_error:
                            logging.error(f"Failed to parse/parameterize UNWIND statement: {parse_error}")
                            logging.error(f"Problematic statement section: {stmt[:250]}...")
                            raise # Re-raise to stop execution
                    else:
                        # Execute non-UNWIND statements directly (e.g., CREATE INDEX)
                        session.execute_write(lambda tx: tx.run(stmt))

                    count += 1
                    if count % 10 == 0 or count == total: # Log progress less frequently
                         logging.info(f"  Executed {count}/{total} statements...")
                except Exception as e:
                    logging.error(f"Error executing statement: {stmt[:150]}... \nError: {e}")
                    # Decide whether to stop or continue on error
                    raise # Re-raise the exception to stop the process on error
            logging.info(f"Successfully executed {count} statements.")

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
