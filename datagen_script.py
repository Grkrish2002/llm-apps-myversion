# Required Packages:
# (No external packages strictly required beyond standard library)
# Standard library: os, sys, json, logging, random, datetime, decimal

import datetime
import random
import os
import sys
import json
import logging
from decimal import Decimal # For precise number handling if needed
import re
import calendar # For more accurate month calculations

# --- Configuration Constants ---
# These will be derived from the input JSON, but we set defaults for standalone running/testing
INPUT_FILENAMES_STR = '{"schema_analysis_filename": "schema_analysis.json", "generation_plan_filename": "generation_plan.json", "value_lists_filename": "value_lists.json", "cardinality_rules_filename": "cardinality_rules.json", "generation_rules_filename": "generation_rules.json"}'
DATE_CONSISTENCY_FLAG_STR = 'true'

try:
    INPUT_FILENAMES = json.loads(INPUT_FILENAMES_STR)
    SCHEMA_FILENAME = INPUT_FILENAMES.get("schema_analysis_filename", "schema_analysis.json")
    PLAN_FILENAME = INPUT_FILENAMES.get("generation_plan_filename", "generation_plan.json")
    VALUES_FILENAME = INPUT_FILENAMES.get("value_lists_filename", "value_lists.json")
    CARDINALITY_FILENAME = INPUT_FILENAMES.get("cardinality_rules_filename") # Optional, can be None
    GENERATION_RULES_FILENAME = INPUT_FILENAMES.get("generation_rules_filename", "generation_rules.json")
except json.JSONDecodeError:
    logging.error("CRITICAL ERROR: Invalid JSON string for input filenames.")
    sys.exit(1)
except Exception as e:
    logging.error(f"CRITICAL ERROR: Error processing input filenames JSON: {e}")
    sys.exit(1)

# Output filename
OUTPUT_CYPHER_FILENAME = "generated_data.cypher"

# --- Log File Configuration ---
LOG_FILENAME = "datagen_script.log"

# --- Basic Logging Setup ---
# Determine script directory for placing the log file
try:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # Fallback if __file__ is not defined (e.g., in interactive environment)
    SCRIPT_DIR = os.getcwd()

LOG_FILE_PATH = os.path.join(SCRIPT_DIR, LOG_FILENAME)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout), # Log to console
        logging.FileHandler(LOG_FILE_PATH, mode='w', encoding='utf-8') # Log to file, 'w' overwrites
    ]
)
logging.info(f"Logging to console and to file: {LOG_FILE_PATH}")

# Global flag from input config
try:
    ENFORCE_DATE_CONSISTENCY = json.loads(DATE_CONSISTENCY_FLAG_STR.lower())
    if not isinstance(ENFORCE_DATE_CONSISTENCY, bool):
        raise ValueError("Date consistency flag must be a boolean (true/false).")
except (json.JSONDecodeError, ValueError) as e:
    logging.error(f"CRITICAL ERROR: Invalid date consistency flag '{DATE_CONSISTENCY_FLAG_STR}': {e}")
    sys.exit(1)

# --- Helper Functions ---

def parse_date_string(date_str, as_datetime=False):
    """
    Parses special date strings like 'NOW', 'NOW_DATETIME', 'TODAY', 'current_year',
    absolute ISO dates/datetimes, and relative dates (e.g., 'NOW-2Y', '2023-01-01+3M').
    Returns a datetime.datetime object if as_datetime is True, otherwise a datetime.date object.
    """
    # Preprocessing: Remove common suffixes like _DATETIME or _DATE from the end of the string
    # This allows inputs like "-3Y_DATETIME" to be treated as "-3Y".
    # It's important to do this before converting to uppercase for keyword matching if suffixes might vary in case.
    cleaned_date_str = re.sub(r"(_DATETIME|_DATE)$", "", date_str.strip(), flags=re.IGNORECASE)

    date_str_upper = cleaned_date_str.upper() # Use cleaned string for uppercase comparison
    current_dt_moment = datetime.datetime.now()
    current_date_moment = current_dt_moment.date()

    # 1. Exact keywords
    if date_str_upper == "NOW":
        return current_dt_moment if as_datetime else current_date_moment
    if date_str_upper == "NOW_DATETIME": # This keyword implies a full datetime moment
        return current_dt_moment # Return the full datetime; caller uses as_datetime to decide final format
    if date_str_upper == "TODAY": # Added for consistency
        return current_dt_moment if as_datetime else current_date_moment
    if date_str_upper == "CURRENT_YEAR": # Assuming this means start of current year
        dt = datetime.datetime(current_date_moment.year, 1, 1, 0, 0, 0) # Explicitly set time to midnight
        return dt if as_datetime else dt.date()

    # 2. Relative dates: BASE<op><val><unit> (e.g., "NOW-2Y", "2023-01-01T10:00:00+3M")
    #    or just <op><val><unit> (e.g., "-2Y", "+3M")
    # Regex to capture optional base, and mandatory sign, value, unit.
    # Allows for ISO dates/datetimes (including 'T' and 'Z') as base.
    relative_match = re.match(r"^(NOW_DATETIME|NOW|TODAY|[\d\-/:.TZ]+)?\s*([+-])\s*(\d+)\s*([YMDH])$", cleaned_date_str, re.IGNORECASE)
    if relative_match:
        base_date_part_str, sign_str, value_str, unit_str = relative_match.groups()
        value = int(value_str)
        if sign_str == '-':
            value = -value

        base_dt = current_dt_moment # Default base is now

        if base_date_part_str: # If a base part was provided
            # Recursively parse the base part, ensuring it's a datetime for calculations
            parsed_base = parse_date_string(base_date_part_str, as_datetime=True) # Always parse base as datetime for arithmetic
            if parsed_base:
                base_dt = parsed_base
            else: # Could not parse the explicit base
                logging.warning(f"Could not parse base '{base_date_part_str}' in relative date '{cleaned_date_str}'. Using current datetime as base.")
        
        calculated_dt = None # Initialize before try block
        try:
            if unit_str.upper() == 'Y':
                calculated_dt = base_dt.replace(year=base_dt.year + value)
            elif unit_str.upper() == 'M':
                month = base_dt.month - 1 + value  # 0-indexed
                year = base_dt.year + month // 12
                month = month % 12 + 1  # 1-indexed
                day = min(base_dt.day, calendar.monthrange(year, month)[1])
                calculated_dt = base_dt.replace(year=year, month=month, day=day)
            elif unit_str.upper() == 'D':
                calculated_dt = base_dt + datetime.timedelta(days=value)
            elif unit_str.upper() == 'H': # Hours
                # Ensure base_dt is a datetime object for hour operations
                if isinstance(base_dt, datetime.date) and not isinstance(base_dt, datetime.datetime):
                    base_dt = datetime.datetime.combine(base_dt, datetime.time.min) # Convert date to datetime at midnight
                calculated_dt = base_dt + datetime.timedelta(hours=value)
            
            if calculated_dt:
                return calculated_dt if as_datetime else calculated_dt.date()

        except ValueError as e: # Handles errors like Feb 29 in non-leap year
            logging.warning(f"Date calculation error for '{cleaned_date_str}' with base '{base_dt}': {e}. Fallback.")
            # Fallback to current moment if calculation fails
            return current_dt_moment if as_datetime else current_date_moment

    # 3. Try standard ISO date/datetime format
    try:
        if as_datetime:
            # Handle Z for UTC explicitly if present
            return datetime.datetime.fromisoformat(cleaned_date_str.replace('Z', '+00:00'))
        else:
            # If expecting a date, but a datetime string is given, parse then take date part
            # Check for 'T' (common datetime separator) or multiple colons (time component)
            if 'T' in cleaned_date_str or cleaned_date_str.count(':') > 1:
                return datetime.datetime.fromisoformat(cleaned_date_str.replace('Z', '+00:00')).date()
            return datetime.date.fromisoformat(cleaned_date_str)
    except ValueError:
        pass
    
    # Fallback if all parsing fails
    # Log the original date_str for better debugging, and the cleaned_date_str to see what was attempted
    logging.warning(f"Could not parse date string '{date_str}' (attempted as '{cleaned_date_str}'). "
                    f"Returning current {'datetime' if as_datetime else 'date'}.")
    return current_dt_moment if as_datetime else current_date_moment


def generate_random_date(start_date_str, end_date_str):
    """Generates a random date between start_date and end_date."""
    start_date = parse_date_string(start_date_str, as_datetime=False)
    end_date = parse_date_string(end_date_str, as_datetime=False)

    if start_date > end_date:
        logging.warning(f"Start date '{start_date_str}' ({start_date}) is after end date '{end_date_str}' ({end_date}). Swapping them.")
        start_date, end_date = end_date, start_date

    time_between_dates = end_date - start_date
    days_between_dates = time_between_dates.days
    if days_between_dates < 0: days_between_dates = 0 # Ensure non-negative

    random_number_of_days = random.randrange(days_between_dates + 1)
    random_date = start_date + datetime.timedelta(days=random_number_of_days)
    return random_date


def generate_random_datetime(start_date_str, end_date_str):
    """Generates a random datetime between start_date and end_date."""
    start_datetime = parse_date_string(start_date_str, as_datetime=True)
    end_datetime = parse_date_string(end_date_str, as_datetime=True)
    if start_datetime > end_datetime:
        logging.warning(f"Start datetime '{start_date_str}' is after end datetime '{end_date_str}'. Swapping them.")
        start_datetime, end_datetime = end_datetime, start_datetime

    time_between_datetimes = end_datetime - start_datetime
    seconds_between_datetimes = time_between_datetimes.total_seconds()
    if seconds_between_datetimes < 0: seconds_between_datetimes = 0

    random_number_of_seconds = random.uniform(0, seconds_between_datetimes)
    random_datetime = start_datetime + datetime.timedelta(seconds=random_number_of_seconds)
    return random_datetime

def generate_property_value(owner_type, qualified_prop_name, prop_type, value_lists_data, generation_rules_data, context_props=None):
    """Generates a single property value based on type, rules, and context."""
    if context_props is None:
        context_props = {} # Ensure context_props is always a dict

    simple_prop_name = qualified_prop_name.split('.')[-1]

    # 1. String Type (Non-ID)
    if prop_type == "String":
        values = value_lists_data.get(owner_type, {}).get(simple_prop_name, [])
        if values:
            return random.choice(values)
        else:
            logging.warning(f"No value list found for String property '{qualified_prop_name}'. Returning empty string.")
            return ""

    # 2. Other Types (Integer, Float, Date, DateTime, Boolean)
    prop_type_lower = prop_type.lower()
    rules_for_type = generation_rules_data.get('type_ranges', {}).get(prop_type_lower, {})

    # Find the rule: specific first, then default
    rule = rules_for_type.get(qualified_prop_name)
    if rule is None:
        rule = rules_for_type.get('default')

    if rule is None:
        # Only log a warning if the property type is NOT Boolean and no rule is found.
        if prop_type != "Boolean":
            logging.warning(f"No generation rule (specific or default) found for '{qualified_prop_name}' of type '{prop_type}'. Using basic default.")
        if prop_type == "Integer": return 0
        if prop_type == "Float": return 0.0
        if prop_type == "Boolean": return random.choice([True, False])
        if prop_type == "Date": return datetime.date.today()
        if prop_type == "DateTime": return datetime.datetime.now()
        return None # Fallback for unknown types

    try:
        if prop_type == "Integer":
            if isinstance(rule, list) and len(rule) == 2:
                min_val, max_val = rule[0], rule[1]
            else:
                logging.warning(f"Invalid or missing integer rule format for '{qualified_prop_name}'. Expected list [min, max]. Using default [0, 100]. Rule: {rule}")
                min_val, max_val = 0, 100 # Hardcoded default fallback

            if min_val > max_val: min_val, max_val = max_val, min_val # Ensure min <= max
            return random.randint(min_val, max_val)


        elif prop_type == "Float":
            min_val, max_val = 0.0, 1.0 # Default values for fallback
            decimals = 2 # Default decimals

            if isinstance(rule, list) and len(rule) == 2:
                try:
                    min_val = float(rule[0])
                    max_val = float(rule[1])
                except (ValueError, TypeError):
                    logging.warning(f"Invalid float rule values for '{qualified_prop_name}'. Expected numbers. Using default [{min_val}, {max_val}]. Rule: {rule}")
            else:
                logging.warning(f"Invalid or missing float rule format for '{qualified_prop_name}'. Expected list [min, max]. Using default [{min_val}, {max_val}]. Rule: {rule}")
            # If rule was not a list of 2, min_val/max_val remain the initial defaults.

            if min_val > max_val: min_val, max_val = max_val, min_val # Ensure min <= max
            val = random.uniform(min_val, max_val)
            return round(val, decimals)


        elif prop_type == "Boolean":
            # Check if a rule exists and is a dictionary specifying probability
            prob_true = 0.5 # Default probability
            if isinstance(rule, dict) and 'probability_true' in rule:
                 try:
                     prob_true = float(rule['probability_true'])
                     if not 0.0 <= prob_true <= 1.0:
                         logging.warning(f"Boolean probability rule for '{qualified_prop_name}' out of range [0.0, 1.0]. Using default 0.5. Rule: {rule}")
                         prob_true = 0.5
                 except (ValueError, TypeError):
                      logging.warning(f"Invalid boolean probability rule format for '{qualified_prop_name}'. Expected float. Using default 0.5. Rule: {rule}")
            elif rule is not None: # Rule exists but isn't a dict with probability_true
                 logging.warning(f"Boolean rule for '{qualified_prop_name}' is not a dictionary with 'probability_true'. Using default 0.5. Rule: {rule}")
            # If rule is None (as per rule generator instructions), prob_true remains 0.5
            return random.random() < prob_true

        elif prop_type == "Date" or prop_type == "DateTime":
            if isinstance(rule, list) and len(rule) == 2:
                start_date_str, end_date_str = rule[0], rule[1]
            else:
                logging.warning(f"Invalid or missing date/datetime rule format for '{qualified_prop_name}'. Expected list [start_date, end_date]. Using default ['-1Y', 'NOW']. Rule: {rule}")
                start_date_str, end_date_str = '-1Y', 'NOW' # Hardcoded default fallback
            generated_dt = None
            if prop_type == "Date":
                generated_dt = generate_random_date(start_date_str, end_date_str)
            else: # DateTime
                generated_dt = generate_random_datetime(start_date_str, end_date_str)

            # --- Date Consistency Placeholder ---
            if ENFORCE_DATE_CONSISTENCY and context_props:
                 # Example: Ensure relationship date is after related node dates
                 # This requires context_props to contain relevant dates and property names
                 # e.g., context_props = {'source_node_created_date': date(...), 'target_node_signup_date': date(...)}
                 earliest_allowed_date = None # Find the latest date among relevant context props
                 # for key, date_val in context_props.items():
                 #     if isinstance(date_val, (datetime.date, datetime.datetime)):
                 #         # Convert date to datetime if comparing with datetime
                 #         context_dt = datetime.datetime.combine(date_val, datetime.time.min) if isinstance(date_val, datetime.date) else date_val
                 #         if earliest_allowed_date is None or context_dt > earliest_allowed_date:
                 #              earliest_allowed_date = context_dt

                 # if earliest_allowed_date:
                 #     current_generated_dt = datetime.datetime.combine(generated_dt, datetime.time.min) if isinstance(generated_dt, datetime.date) else generated_dt
                 #     if current_generated_dt < earliest_allowed_date:
                 #         logging.debug(f"Date consistency enforced for {qualified_prop_name}: Adjusted {generated_dt} to be after {earliest_allowed_date}")
                 #         # Regenerate within a valid range or clamp (simplistic clamp shown)
                 #         # This logic needs careful design based on specific consistency needs
                 #         generated_dt = earliest_allowed_date.date() if isinstance(generated_dt, datetime.date) else earliest_allowed_date
                 pass # Implement actual consistency logic here based on rules and context needed

            return generated_dt

        else:
            logging.warning(f"Unsupported property type '{prop_type}' in generation rule application for '{qualified_prop_name}'. Returning None.")
            return None

    except Exception as e:
        logging.error(f"Error applying generation rule for '{qualified_prop_name}' (Rule: {rule}): {e}")
        # Return basic default on error
        if prop_type == "Integer": return 0
        if prop_type == "Float": return 0.0
        if prop_type == "Boolean": return False
        if prop_type == "Date": return datetime.date.today()
        if prop_type == "DateTime": return datetime.datetime.now()
        return None

def load_config_data(script_dir):
    """Loads all required and optional configuration files."""
    config_data = {}
    required_files = {
        "schema_data": SCHEMA_FILENAME,
        "plan_data": PLAN_FILENAME,
        "value_lists_data": VALUES_FILENAME,
        "generation_rules_data": GENERATION_RULES_FILENAME,
    }
    optional_files = {}
    if CARDINALITY_FILENAME: # Only add if specified
        optional_files["cardinality_rules_data"] = CARDINALITY_FILENAME

    # Load required files
    for key, filename in required_files.items():
        filepath = os.path.join(script_dir, filename)
        logging.info(f"Attempting to load required file: {filepath}")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                config_data[key] = json.load(f)
            logging.info(f"Successfully loaded {filename}")
        except FileNotFoundError:
            logging.error(f"CRITICAL ERROR: Required file not found: {filepath}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            logging.error(f"CRITICAL ERROR: Invalid JSON in file {filepath}: {e}")
            sys.exit(1)
        except Exception as e:
            logging.error(f"CRITICAL ERROR: Unexpected error loading {filepath}: {e}")
            sys.exit(1)

    # Load optional files
    for key, filename in optional_files.items():
        filepath = os.path.join(script_dir, filename)
        logging.info(f"Attempting to load optional file: {filepath}")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                config_data[key] = json.load(f)
            logging.info(f"Successfully loaded optional file {filename}")
        except FileNotFoundError:
            logging.warning(f"Optional file not found, defaulting to empty: {filepath}")
            config_data[key] = {} # Default to empty dict if not found
        except json.JSONDecodeError as e:
            logging.warning(f"Invalid JSON in optional file {filepath}, defaulting to empty: {e}")
            config_data[key] = {} # Default to empty dict if invalid
        except Exception as e:
            logging.warning(f"Unexpected error loading optional {filepath}, defaulting to empty: {e}")
            config_data[key] = {}
    # Ensure key exists even if file was not specified
    if "cardinality_rules_data" not in config_data:
         config_data["cardinality_rules_data"] = {}


    return config_data

def generate_sequential_id(label, id_property_name, id_property_type, counter):
    """Generates a sequential ID based on type."""
    if id_property_type == "String":
        # Use label in ID by default, can be customized
        return f"{label}_{counter:04d}" # e.g., "Customer_0001"
    elif id_property_type == "Integer":
        return counter
    else:
        # Fallback for other types? Could use counter as string or raise error.
        logging.warning(f"Unsupported ID property type '{id_property_type}' for {label}. Using integer counter.")
        return counter

def escape_cypher_string(value):
    """Escapes single quotes and backslashes for Cypher strings."""
    if value is None:
        return ""
    # Replace backslashes first, then single quotes
    return value.replace('\\', '\\\\').replace("'", "\\'")

def format_cypher_value(value):
    """Formats a Python value into a Cypher-compatible string representation."""
    if value is None:
        return "null"
    elif isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, (int, float)):
        return str(value)
    elif isinstance(value, Decimal):
        # Neo4j doesn't have a native Decimal type, typically store as Float or String
        # return str(value) # Store as string to preserve precision
        return str(float(value)) # Store as float (potential precision loss)
    elif isinstance(value, str):
        return f"'{escape_cypher_string(value)}'"
    elif isinstance(value, datetime.datetime):
        # Format as ISO 8601 string within datetime() function
        # Consider adding timezone info if available/needed
        return f"datetime('{value.isoformat()}')"
    elif isinstance(value, datetime.date):
        # Format as ISO 8601 string within date() function
        return f"date('{value.isoformat()}')"
    elif isinstance(value, list):
        # Format list elements recursively
        return "[" + ", ".join(format_cypher_value(item) for item in value) + "]"
    else:
        logging.warning(f"Unsupported type for Cypher formatting: {type(value)}. Returning null.")
        return "null"

def format_cypher_properties(props_dict):
    """Formats a dictionary of properties into a Cypher map string."""
    if not props_dict:
        return "{}"
    items = []
    for key, value in props_dict.items():
        # Ensure keys are valid Cypher identifiers (basic check)
        # More robust checking might be needed depending on allowed characters
        # if not key.isidentifier():
        #    logging.warning(f"Property key '{key}' might not be a valid Cypher identifier. Using backticks.")
        #    key_formatted = f"`{key}`" # Use backticks for keys with special chars
        # else:
        key_formatted = key # Assume keys are simple for now

        formatted_value = format_cypher_value(value)
        items.append(f"{key_formatted}: {formatted_value}")
    return "{ " + ", ".join(items) + " }"

def write_cypher(file_handle, statement):
    """Writes a Cypher statement to the file handle, followed by a semicolon and newline."""
    file_handle.write(statement + ";\n")

def custom_json_serializer(obj):
    """Custom JSON serializer for datetime and Decimal objects."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    elif isinstance(obj, Decimal):
        return str(obj) # Store Decimal as string to preserve precision for parameters
    raise TypeError(f"Type {type(obj)} not serializable for JSON parameters")


# --- Main Execution Block ---
if __name__ == "__main__":
    logging.info("Starting Neo4j data generation script...")
    try:
        # Determine script directory relative to the executed file
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        # Fallback if __file__ is not defined (e.g., in interactive environment)
        script_dir = os.getcwd()
        logging.warning(f"Could not determine script directory via __file__, using current working directory: {script_dir}")


    # 1. Load Config
    loaded_configs = load_config_data(script_dir)
    schema_data = loaded_configs["schema_data"]
    plan_data = loaded_configs["plan_data"]
    value_lists_data = loaded_configs["value_lists_data"]
    cardinality_rules_data = loaded_configs["cardinality_rules_data"]
    generation_rules_data = loaded_configs["generation_rules_data"]

    # Extract node counts
    node_counts_to_generate = plan_data # plan_data is now a direct mapping of label -> count
    if not node_counts_to_generate:
         logging.error("CRITICAL ERROR: 'node_counts' key not found or empty in generation plan file.")
         sys.exit(1)

    logging.info(f"Date consistency enforcement: {ENFORCE_DATE_CONSISTENCY}")
    # Placeholder for using additional_instructions if they were passed
    # additional_instructions = loaded_configs.get("additional_instructions", "") # Example
    # if additional_instructions:
    #     logging.info(f"Additional instructions loaded: {additional_instructions}")


    # 2. Initialize Storage
    generated_data = {
        "nodes": {}, # Stores lists of generated IDs grouped by label: {"Customer": [id1, id2,...]}
        "nodes_for_cypher": [], # Stores list of full property dictionaries for nodes: [{prop_dict_1}, ...]
        "relationships_for_cypher": [] # Stores list of relationship details for cypher
    }
    node_counters = {label: 1 for label in node_counts_to_generate} # Reset counters each run


    # 3. Perform Generation

    # --- Node Generation ---
    logging.info("Starting node generation...")
    generated_data["nodes"] = {label: [] for label in node_counts_to_generate}
    generated_data["nodes_for_cypher"] = []

    # Find ID properties from schema first for validation and use
    node_id_props = {}
    schema_nodes = schema_data.get("nodes", {})
    if not schema_nodes:
         logging.error("CRITICAL ERROR: 'nodes' definition missing or empty in schema file.")
         sys.exit(1)

    for label, details in schema_nodes.items():
        id_prop_name = details.get("id_property")
        if not id_prop_name:
            logging.error(f"CRITICAL ERROR: No 'id_property' defined for node label '{label}' in schema.")
            sys.exit(1)
        
        # Correctly extract id_prop_type from the list of property dictionaries
        id_prop_type = None
        properties_list = details.get("properties", []) # 'properties' is a list
        if isinstance(properties_list, list):
            for prop_dict in properties_list:
                if isinstance(prop_dict, dict) and prop_dict.get("name") == id_prop_name:
                    id_prop_type = prop_dict.get("type")
                    break # Found the ID property
        else:
            logging.error(f"CRITICAL ERROR: 'properties' for label '{label}' is not a list in schema.")
            sys.exit(1)

        if not id_prop_type:
            logging.error(f"CRITICAL ERROR: ID property '{id_prop_name}' not found or has no type for label '{label}' in schema.")
            sys.exit(1)
        node_id_props[label] = {"name": id_prop_name, "type": id_prop_type}

    # Generate nodes based on the plan
    for label, count in node_counts_to_generate.items():
        logging.info(f"Generating {count} nodes for label: {label}")
        if label not in schema_nodes:
            logging.warning(f"Skipping node generation for label '{label}': Not found in schema.")
            continue
        if label not in node_id_props:
             # This case should be caught by the previous check, but double-check
            logging.warning(f"Skipping node generation for label '{label}': ID property info missing (schema error).")
            continue

        label_schema = schema_nodes[label]
        id_prop_info = node_id_props[label]
        id_prop_name = id_prop_info["name"]
        id_prop_type = id_prop_info["type"]
        label_properties_schema = label_schema.get("properties", []) # properties is a list of dicts

        for i in range(count):
            node_props = {}
            current_count = node_counters[label]

            # 1. Generate ID property
            generated_id = generate_sequential_id(label, id_prop_name, id_prop_type, current_count)
            node_props[id_prop_name] = generated_id
            node_counters[label] += 1

            # 2. Generate other properties
            for prop_detail_dict in label_properties_schema: # Iterate directly over the list
                if not isinstance(prop_detail_dict, dict): continue # Skip if not a dict
                prop_name = prop_detail_dict.get("name")
                
                if prop_name == id_prop_name:
                    continue # Skip ID property, already generated

                prop_type = prop_detail_dict.get("type") # Corrected variable name
                if not prop_type:
                    logging.warning(f"Property '{prop_name}' for label '{label}' has no type defined in schema. Skipping.")
                    continue

                qualified_prop_name = f"{label}.{prop_name}"
                # Pass None for context_props for nodes initially
                prop_value = generate_property_value(
                    label, qualified_prop_name, prop_type,
                    value_lists_data, generation_rules_data, context_props=None
                )
                if prop_value is not None: # Avoid adding properties with None value unless explicitly intended
                    node_props[prop_name] = prop_value

            # 3. Store generated node data
            # Add labels info for potential grouping later during Cypher generation
            node_props["_labels"] = [label] # Internal use, remove before final Cypher formatting

            generated_data["nodes"][label].append(generated_id) # Store the actual ID value
            generated_data["nodes_for_cypher"].append(node_props)

            if (i + 1) % 1000 == 0 or (i + 1) == count : # Log progress every 1000 and at the end
                 logging.info(f"Generated {i + 1}/{count} nodes for {label}...")

    logging.info("Node generation complete.")


    # --- Relationship Generation ---
    logging.info("Starting relationship generation...")
    generated_data["relationships_for_cypher"] = []

    # 'relationships' from schema_analysis.json is a LIST of relationship definition objects.
    relationship_definitions_list = schema_data.get("relationships", [])
    if not relationship_definitions_list:
        logging.warning("No relationship definitions found in schema. Skipping relationship generation.")
    else:
        # Node property lookup might be needed for date consistency checks
        # (Existing commented-out code for node_lookup_by_id remains here)
        # Creating a full lookup can be memory intensive. Avoid if possible or optimize.
        # node_lookup_by_id = {}
        # if ENFORCE_DATE_CONSISTENCY:
        #     logging.info("Building node lookup for date consistency checks...")
        #     for props in generated_data['nodes_for_cypher']:
        #         label = props['_labels'][0]
        #         id_prop_name = node_id_props[label]['name']
        #         node_id = props[id_prop_name]
        #         node_lookup_by_id[(label, node_id)] = props # Key by (label, id) tuple
        #     logging.info("Node lookup built.")


        for rel_definition_object in relationship_definitions_list: # Iterate over the list
            if not isinstance(rel_definition_object, dict):
                logging.warning(f"Skipping invalid relationship definition (not a dict): {rel_definition_object}")
                continue

            rel_type = rel_definition_object.get("type")
            source_label = rel_definition_object.get("source") # Key for source label
            target_label = rel_definition_object.get("target") # Key for target label
            # 'properties' within each relationship definition is also a LIST of property dicts.
            properties_schema_list = rel_definition_object.get("properties", [])

            if not all([rel_type, source_label, target_label]): # Basic validation
                logging.warning(f"Skipping relationship definition due to missing 'type', 'source', or 'target': {rel_definition_object}")
                continue
            logging.info(f"Generating relationships of type: ({source_label})-[:{rel_type}]->({target_label})")

            if not source_label or not target_label:
                logging.warning(f"Skipping relationship type '{rel_type}': Source ('{source_label}') or target ('{target_label}') label missing in schema.")
                continue

            source_ids = generated_data["nodes"].get(source_label, [])
            target_ids = generated_data["nodes"].get(target_label, [])

            if not source_ids or not target_ids:
                logging.warning(f"Skipping relationship type '{rel_type}': No generated nodes found for source '{source_label}' ({len(source_ids)}) or target '{target_label}' ({len(target_ids)}).")
                continue

            if source_label not in node_id_props or target_label not in node_id_props:
                 logging.warning(f"Skipping relationship type '{rel_type}': Missing ID property info for source '{source_label}' or target '{target_label}' label in schema.")
                 continue

            source_id_prop_name = node_id_props[source_label]["name"]
            target_id_prop_name = node_id_props[target_label]["name"]

            generated_count = 0
            cardinality_rule = cardinality_rules_data.get(rel_type)

            if cardinality_rule and isinstance(cardinality_rule, dict):
                # --- Strategy 1: Use Cardinality Rule ---
                min_rels = cardinality_rule.get("min", 0) # Default min to 0 if not specified
                max_rels = cardinality_rule.get("max", 1) # Default max to 1 if not specified
                # Ensure min <= max
                if min_rels > max_rels:
                    logging.warning(f"Cardinality rule for '{rel_type}' has min ({min_rels}) > max ({max_rels}). Clamping max to min.")
                    max_rels = min_rels
                # Ensure max is not greater than available targets
                max_rels = min(max_rels, len(target_ids))
                if min_rels > max_rels:
                    logging.warning(f"Cardinality rule for '{rel_type}' requires min ({min_rels}) > available targets ({len(target_ids)} after clamping max). Setting min to {max_rels}.")
                    min_rels = max_rels


                logging.info(f"Using cardinality rule for '{rel_type}': min={min_rels}, max={max_rels} per source.")

                if max_rels == 0 and min_rels == 0:
                     logging.info(f"Cardinality rule for '{rel_type}' specifies 0 relationships. Skipping.")
                     continue # Skip if max relationships is 0

                # Make target_ids usable for sampling
                available_target_ids = list(target_ids)

                for source_id in source_ids:
                    if not available_target_ids:
                        logging.warning(f"Ran out of available unique targets for '{rel_type}' while processing source {source_id}. Stopping relationship generation for this source.")
                        break # No more targets globally

                    # Determine how many relationships to create for this source node
                    num_rels_for_source = random.randint(min_rels, max_rels)

                    # Ensure we don't try to select more targets than are available
                    num_to_select = min(num_rels_for_source, len(available_target_ids))

                    if num_to_select <= 0:
                         continue # Skip if 0 relationships needed for this source

                    # Select unique targets for this source
                    # random.sample requires k <= population size
                    selected_target_ids = random.sample(available_target_ids, k=num_to_select)
                    # Note: This samples *without* replacement from the perspective of this single source node.
                    # It does NOT currently prevent the same target from being picked by multiple sources.
                    # If global target uniqueness per relationship type is needed, selected targets
                    # would need to be removed from available_target_ids (more complex).

                    for target_id in selected_target_ids:
                        # Generate properties (potentially with context)
                        rel_props = {}
                        context = None
                        # --- Context preparation for date consistency (Example) ---
                        # if ENFORCE_DATE_CONSISTENCY:
                        #     try:
                        #         source_node_props = node_lookup_by_id.get((source_label, source_id))
                        #         target_node_props = node_lookup_by_id.get((target_label, target_id))
                        #         context = {}
                        #         # Pass relevant properties (e.g., creation dates) to generator
                        #         # Need to know property names from schema/rules
                        #         # if source_node_props and 'createdDate' in source_node_props:
                        #         #    context['source_node_created_date'] = source_node_props['createdDate']
                        #         # if target_node_props and 'signupDate' in target_node_props:
                        #         #    context['target_node_signup_date'] = target_node_props['signupDate']
                        #     except KeyError:
                        #         logging.warning(f"Could not find source/target node data for consistency check for rel ({source_id})-[:{rel_type}]->({target_id})")
                        #         context = None # Fallback if node lookup fails
                        # --- End Context preparation ---

                        # Iterate over the list of property definition dictionaries
                        for prop_detail_dict in properties_schema_list:
                            if not isinstance(prop_detail_dict, dict):
                                logging.warning(f"Skipping invalid property detail in relationship '{rel_type}' (not a dict): {prop_detail_dict}")
                                continue
                            prop_name = prop_detail_dict.get("name")
                            prop_type = prop_detail_dict.get("type")

                            if prop_name and prop_type:
                                qualified_prop_name = f"{rel_type}.{prop_name}"
                                prop_value = generate_property_value(
                                    rel_type, qualified_prop_name, prop_type,
                                    value_lists_data, generation_rules_data, context_props=context
                                )
                                if prop_value is not None:
                                    rel_props[prop_name] = prop_value

                        # Store relationship details
                        generated_data["relationships_for_cypher"].append({
                            "source_label": source_label,
                            "source_id": source_id,
                            "target_label": target_label,
                            "target_id": target_id,
                            "rel_type": rel_type,
                            "properties": rel_props
                        })
                        generated_count += 1

            else:
                # --- Strategy 2: Hybrid Default Cardinality ---
                logging.info(f"Using hybrid default cardinality for '{rel_type}' (no rule found or rule invalid).")
                source_count = len(source_ids)
                target_count = len(target_ids)
                num_rels_to_create = min(source_count, target_count)

                if num_rels_to_create == 0:
                     logging.warning(f"Cannot create default relationships for '{rel_type}': one side has 0 nodes.")
                     continue

                logging.info(f"Attempting to create {num_rels_to_create} unique relationships for '{rel_type}' (min of {source_count} sources, {target_count} targets).")

                # Identify smaller/larger sets for efficient pairing
                if source_count <= target_count:
                    smaller_ids = list(source_ids) # Copy to shuffle
                    larger_ids = list(target_ids)  # Copy to shuffle
                    is_source_smaller = True
                else:
                    smaller_ids = list(target_ids)  # Copy to shuffle
                    larger_ids = list(source_ids) # Copy to shuffle
                    is_source_smaller = False

                random.shuffle(smaller_ids)
                random.shuffle(larger_ids)

                # Pair each element of the smaller list with a unique element from the larger list
                for i in range(num_rels_to_create):
                    id_from_smaller = smaller_ids[i]
                    id_from_larger = larger_ids[i] # Pair with corresponding shuffled element

                    # Assign source/target based on which set was smaller
                    current_source_id = id_from_smaller if is_source_smaller else id_from_larger
                    current_target_id = id_from_larger if is_source_smaller else id_from_smaller

                    # Generate properties
                    rel_props = {}
                    context = None
                    # --- Context preparation (similar to above) ---
                    # if ENFORCE_DATE_CONSISTENCY:
                    #     # ... fetch source/target node data ...
                    #     pass
                    # --- End Context preparation ---

                    # Iterate over the list of property definition dictionaries
                    for prop_detail_dict in properties_schema_list:
                        if not isinstance(prop_detail_dict, dict):
                            logging.warning(f"Skipping invalid property detail in relationship '{rel_type}' (not a dict): {prop_detail_dict}")
                            continue
                        prop_name = prop_detail_dict.get("name")
                        prop_type = prop_detail_dict.get("type")

                        if prop_name and prop_type:
                            qualified_prop_name = f"{rel_type}.{prop_name}"
                            prop_value = generate_property_value(
                                 rel_type, qualified_prop_name, prop_type,
                                 value_lists_data, generation_rules_data, context_props=context
                            )
                            if prop_value is not None:
                                rel_props[prop_name] = prop_value

                    # Store relationship details
                    generated_data["relationships_for_cypher"].append({
                        "source_label": source_label,
                        "source_id": current_source_id,
                        "target_label": target_label,
                        "target_id": current_target_id,
                        "rel_type": rel_type,
                        "properties": rel_props
                    })
                    generated_count += 1

            logging.info(f"Generated {generated_count} relationships of type '{rel_type}'.")

    logging.info("Relationship generation complete.")


    # 4. Write Cypher File
    logging.info(f"Generating Cypher script: {OUTPUT_CYPHER_FILENAME}")
    output_filepath = os.path.join(script_dir, OUTPUT_CYPHER_FILENAME)

    try:
        with open(output_filepath, 'w', encoding='utf-8') as f:
            write_cypher(f, f"// Generated by generate_neo4j_data.py on {datetime.datetime.now().isoformat()}")
            write_cypher(f, f"// Schema: {SCHEMA_FILENAME}")
            write_cypher(f, f"// Plan: {PLAN_FILENAME}")
            if CARDINALITY_FILENAME:
                write_cypher(f, f"// Cardinality Rules: {CARDINALITY_FILENAME}")
            write_cypher(f, f"// Generation Rules: {GENERATION_RULES_FILENAME}")
            write_cypher(f, f"// Date Consistency Enforced: {ENFORCE_DATE_CONSISTENCY}")
            write_cypher(f, f"// Total Nodes Planned: {sum(node_counts_to_generate.values())}")
            write_cypher(f, f"// Total Nodes Generated: {len(generated_data['nodes_for_cypher'])}")
            write_cypher(f, f"// Total Relationships Generated: {len(generated_data['relationships_for_cypher'])}")
            write_cypher(f, "")

            # Add Index/Constraint Recommendations (commented out)
            write_cypher(f, "// --- Recommended Indexes/Constraints (run manually before loading data) ---")
            for label, id_info in node_id_props.items():
                id_name = id_info['name']
                # Recommend unique constraint for the designated ID property
                write_cypher(f, f"// CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{id_name} IS UNIQUE;")
            write_cypher(f, "")


            # --- Generate Node Cypher using UNWIND ---
            logging.info("Writing node creation Cypher...")
            nodes_grouped_by_label = {}
            for node_prop_dict in generated_data["nodes_for_cypher"]:
                # Extract label from the internal '_labels' key
                label = node_prop_dict.get('_labels', [None])[0]
                if label:
                    if label not in nodes_grouped_by_label:
                        nodes_grouped_by_label[label] = []
                    # Create a copy without the internal '_labels' key for Cypher
                    final_props = {k: v for k, v in node_prop_dict.items() if k != '_labels'}
                    nodes_grouped_by_label[label].append(final_props)

            for label, node_batch in nodes_grouped_by_label.items():
                if not node_batch: continue

                id_prop_name = node_id_props[label]["name"]
                param_name = f"nodes_{label}"

                write_cypher(f, f"// --- Creating nodes for Label: {label} ---")
                # Define the parameter with the actual data
                json_data_string = json.dumps(node_batch, default=custom_json_serializer, indent=None) # No indent for :param
                f.write(f":param {param_name} => {json_data_string};\n") # Write :param line

                write_cypher(f, f"UNWIND ${param_name} AS node_props")
                # MERGE might be safer if script can be rerun, CREATE assumes clean slate
                # Use MERGE on the ID property to ensure idempotency
                write_cypher(f, f"MERGE (n:{label} {{ {id_prop_name}: node_props.{id_prop_name} }})")
                write_cypher(f, f"SET n += node_props") # Use += to add/update other properties
                write_cypher(f, "") # Blank line separator


            # --- Generate Relationship Cypher using UNWIND and MATCH/MERGE ---
            logging.info("Writing relationship creation Cypher...")
            rels_grouped = {}
            for rel_data in generated_data["relationships_for_cypher"]:
                group_key = (rel_data["source_label"], rel_data["rel_type"], rel_data["target_label"])
                if group_key not in rels_grouped:
                    rels_grouped[group_key] = []
                # Store only the necessary info for the UNWIND batch
                rels_grouped[group_key].append({
                    "source_id": rel_data["source_id"],
                    "target_id": rel_data["target_id"],
                    "properties": rel_data["properties"] # Keep properties for SET/MERGE
                })

            for group_key, rel_batch in rels_grouped.items():
                if not rel_batch: continue
                source_label, rel_type, target_label = group_key

                # Check if ID info is available (should be, based on earlier checks)
                if source_label not in node_id_props or target_label not in node_id_props:
                     logging.warning(f"Cannot write Cypher for ({source_label})-[:{rel_type}]->({target_label}): Missing ID property info.")
                     continue

                src_id_prop = node_id_props[source_label]["name"]
                tgt_id_prop = node_id_props[target_label]["name"]

                param_name = f"rels_{source_label}_{rel_type}_{target_label}"

                write_cypher(f, f"// --- Creating relationships: ({source_label})-[:{rel_type}]->({target_label}) ---")
                # Define the parameter with the actual data
                json_data_string = json.dumps(rel_batch, default=custom_json_serializer, indent=None) # No indent for :param
                f.write(f":param {param_name} => {json_data_string};\n") # Write :param line
                write_cypher(f, f"UNWIND ${param_name} AS rel_data")
                # Match source and target nodes using their unique IDs
                write_cypher(f, f"MATCH (a:{source_label} {{ {src_id_prop}: rel_data.source_id }})")
                write_cypher(f, f"MATCH (b:{target_label} {{ {tgt_id_prop}: rel_data.target_id }})")
                # Use MERGE for relationships to make the script idempotent.
                # MERGE requires specifying properties used for uniqueness or merging on the pattern.
                # If rels have no unique properties, MERGE creates one if none exists.
                # If properties should be updated on existing rels, use ON MATCH SET.
                # If properties only set on new rels, use ON CREATE SET.
                # Simplest approach: MERGE the pattern, then SET properties unconditionally.
                write_cypher(f, f"MERGE (a)-[r:{rel_type}]->(b)")
                # SET unconditionally adds/updates properties from the batch
                write_cypher(f, f"SET r = rel_data.properties")
                # Alternative: Only set on creation
                # write_cypher(f, f"ON CREATE SET r = rel_data.properties")
                # Alternative: Set on creation and update on match
                # write_cypher(f, f"ON CREATE SET r = rel_data.properties")
                # write_cypher(f, f"ON MATCH SET r += rel_data.properties") # Use += to merge properties

                write_cypher(f, "") # Blank line separator

        logging.info(f"Successfully generated Cypher script: {output_filepath}")
        logging.info(f"The generated Cypher script now includes the data using ':param' syntax.")
        logging.info(f"You can run this script directly using cypher-shell: cypher-shell < {OUTPUT_CYPHER_FILENAME}")


    except IOError as e:
        logging.error(f"ERROR: Could not write to output file {output_filepath}: {e}")
        sys.exit(1)
    except KeyError as e:
         logging.error(f"ERROR: Missing expected key in configuration or generated data: {e}")
         sys.exit(1)
    except Exception as e:
        logging.exception(f"ERROR: An unexpected error occurred during Cypher generation: {e}") # Log full traceback
        sys.exit(1)

    logging.info("Script finished successfully.")