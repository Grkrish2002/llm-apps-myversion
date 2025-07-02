# This program runs series of agents to generate synthetic data matching Retail ontology to load in Neo4j. 
import streamlit as st
import google.generativeai as genai
from agno.agent import Agent
from agno.models.google import Gemini
import json
import os
import logging
import re
from typing import Any, Optional
from textwrap import dedent
import dotenv # Import dotenv
# import markdown # For potentially displaying markdown plans
# import time # For LLM delays
import math # For hybrid cardinality
from agno.exceptions import ModelProviderError # Import Agno's specific error

# --- Configuration ---

# Define the output directory for intermediate agent outputs
AGENT_OUTPUT_DIR = os.getcwd() # Set to the current working directory

# Define standard filenames for intermediate files
SCHEMA_ANALYSIS_FILENAME = "schema_analysis.json"
GENERATION_PLAN_FILENAME = "generation_plan.json"
VALUE_LISTS_FILENAME = "value_lists.json"
CARDINALITY_RULES_FILENAME = "cardinality_rules.json" # Optional file
GENERATION_RULES_FILENAME = "generation_rules.json" # New file for non-string property rules
PYTHON_CODE_PLAN_FILENAME = "python_code_plan.md"
FAILED_CODE_OUTPUT_FILENAME = "failed_code_generator_output.txt"
RAW_SCHEMA_AGENT_OUTPUT_FILENAME = "schema_analysis_agent_output.json" # Raw output if agent runs
RAW_VALUE_LIST_STREAMED_FILENAME = "value_lists_raw_streamed.json" # Raw streamed output
RAW_CODE_GEN_STREAMED_FILENAME = "code_gen_raw_streamed.txt" # Raw streamed code output
APP_LOG_FILENAME = "agent_workflow.log" # Log file for this Streamlit app

# Load environment variables from a .env file if it exists
dotenv.load_dotenv(override=True)

# --- Logging Setup ---
def setup_logging():
    """Configures logging to both console and a file."""
    # Ensure AGENT_OUTPUT_DIR exists for the log file
    log_dir = os.path.join(os.getcwd(), AGENT_OUTPUT_DIR) # Use AGENT_OUTPUT_DIR for consistency
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, APP_LOG_FILENAME)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(), # Log to console
            logging.FileHandler(log_file_path, mode='a', encoding='utf-8') # Append to log file
        ]
    )
    return logging.getLogger(__name__)

logger = setup_logging() # Initialize logger

# --- Helper Functions: get_available_gemini_models, safe_json_loads, extract_python_code ---
def get_available_gemini_models():
    """Retrieves and returns a list of available Gemini models."""
    try:
        google_api_key = os.getenv("GOOGLE_API_KEY")
        if not google_api_key:
            try:
                google_api_key = st.secrets["GOOGLE_API_KEY"]
            except (KeyError, FileNotFoundError):
                st.error("Google API key not found. Please set the GOOGLE_API_KEY environment variable or add it to Streamlit secrets.")
                st.stop()
        genai.configure(api_key=google_api_key)
        print(f"Configured Gemini with API key: {google_api_key}")
        model_list = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        logger.info(f"Found available Gemini models: {model_list}")
        return model_list
    except Exception as e:
        st.error(f"Error retrieving Gemini models: {e}")
        logger.error(f"Error retrieving Gemini models: {e}", exc_info=True)
        return []

def safe_json_loads(text: str) -> Optional[Any]:
    """Safely loads JSON, attempting to clean common LLM output issues."""
    if not isinstance(text, str):
        logger.error(f"safe_json_loads expected a string, but got {type(text)}")
        return None
    text = text.strip().strip('`')
    if text.lower().startswith("json"):
        text = text[4:].strip()
    start_brace = text.find('{')
    start_bracket = text.find('[') # Look for the start of an array
    end_brace = text.rfind('}')
    end_bracket = text.rfind(']') # Look for the end of an array

    # Find the earliest potential start and latest potential end of a JSON structure
    start = -1
    if start_brace != -1 and start_bracket != -1: start = min(start_brace, start_bracket)
    elif start_brace != -1: start = start_brace
    elif start_bracket != -1: start = start_bracket

    end = -1
    if end_brace != -1 and end_bracket != -1: end = max(end_brace, end_bracket)
    elif end_brace != -1: end = end_brace
    elif end_bracket != -1: end = end_bracket

    json_text = text # Default to the whole text if markers aren't found or are mismatched

    if start != -1 and end != -1 and start < end: json_text = text[start : end + 1]
    # Added a check to see if the extracted text *looks* like JSON before using it
    # This helps avoid cases where random braces/brackets are found.
    # A more sophisticated check could involve counting braces/brackets.
    elif start != -1 and end != -1 and start < end and (json_text.strip().startswith('{') or json_text.strip().startswith('[')):
         json_text = text[start : end + 1]
    else:
        logger.warning(f"Could not reliably find JSON boundaries in text: {text[:100]}... Trying full cleaned text.")
        json_text = text
    try:
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON: {e}\nOriginal text snippet: {json_text[:500]}...")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during JSON parsing: {e}", exc_info=True)
        return None

def extract_python_code(markdown_string: str) -> Optional[str]:
    """Extracts Python code from the first markdown code block."""
    if not isinstance(markdown_string, str):
        logger.error(f"extract_python_code expected a string, but got {type(markdown_string)}")
        return None
    # Handle potential variations in markdown code block start
    match = re.search(r"```(?:python)?\n(.*?)\n```", markdown_string, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    else:
        logger.warning("Could not find ```python ... ``` block. Assuming entire response is code.")
        # More robust check if it looks like Python
        cleaned_string = markdown_string.strip().strip('`')
        if ("import " in cleaned_string or "def " in cleaned_string or "class " in cleaned_string or
            "print(" in cleaned_string or "=" in cleaned_string): # Added more heuristics
             logger.info("Assuming entire response is Python code based on content.")
             return cleaned_string
        else:
             logger.error("Response does not appear to contain Python code.")
             return None

# --- Agents ---
# 1. Schema Analyzer
schema_analyzer = Agent(
    name="SchemaAnalyzer",
    model=None, # Placeholder
    instructions=dedent(f"""\
    You are an expert Neo4j schema analyzer with RETAIL domain expertise. Analyze the provided Neo4j JSON schema. Identify:
    1. All node labels.
    2. For each node label:
        a. Its properties (name, type, constraints like UNIQUE if provided in input).
        b. Determine the best unique identifier property ('id_property').
            - First, look for existing properties that are commonly used as IDs (e.g., ending with 'ID', 'Id', 'Key', 'Code', 'Number', or named 'id').
            - If NO suitable existing property is found, you MUST create a new 'id_property' name. Construct this name by taking the node label, converting its first letter to lowercase (camelCase), and appending "ID" (e.g., for node label "CampaignPerformance", the id_property will be "campaignPerformanceID"). This newly created ID property should be considered of type "String".
    3. All relationship types.
    4. For each relationship type: its type name, source node label, target node label, and properties (name, type).
    5. CRITICAL: If the input JSON's "nodes" dictionary is empty, your output JSON's "nodes" dictionary MUST also be empty. Do NOT infer or include node details if no nodes were provided in the input batch.
    
    Output the analysis as a single, structured JSON object.
    CRITICAL: You MUST return ONLY the raw, valid JSON string.
    - Ensure all elements in JSON lists and all key-value pairs in JSON objects are correctly separated by commas.
    - Do NOT use trailing commas.
    - Do NOT include ```json``` markers or any explanations, introductory text, or conversational remarks.

    The JSON output MUST strictly follow this format:
    ```json
    {{
      "nodes": {{
        "NodeLabel1": {{
          "properties": [
            {{ "name": "property1Name", "type": "String", "constraints": ["UNIQUE"] }},
            {{ "name": "property2Name", "type": "Integer" }},
            {{ "name": "createdIdProperty", "type": "String" }}
          ],
          "id_property": "property1Name" 
        }},
        "NodeLabel2": {{
          "properties": [
            {{ "name": "anotherProp", "type": "Float" }},
            {{ "name": "nodeLabel2ID", "type": "String" }} 
          ],
          "id_property": "nodeLabel2ID" 
        }}
      }},
      "relationships": [
        {{
          "type": "REL_TYPE",
          "source": "NodeLabel1",
          "target": "NodeLabel2",
          "properties": [
            {{ "name": "rel_prop1", "type": "Date" }}
          ]
        }}
      ]
    }}
    ```
    For the "id_property" field, provide ONLY the string name of the property, e.g., "customerID", NOT "id_property": "The ID is customerID".
    """),
    markdown=False,
)

# 2. Data Planner
data_planner = Agent(
    name="DataPlanner",
    model=None, # Placeholder
    instructions="""
    Based on the provided Neo4j schema analysis and specific counts for 'Store' and 'Customer' nodes, propose the number of instances to generate for ALL node labels.

    Input will be a JSON object string containing:
    - 'schema_analysis': The analysis result with node properties and relationships.
    - 'num_stores': An integer representing the desired number of 'Store' nodes.
    - 'num_customers': An integer representing the desired number of 'Customer' nodes.

    Your task:
    1.  **Use Provided Counts:** Set the counts for 'Store' and 'Customer' labels directly from the `num_stores` and `num_customers` input values. If 'Store' or 'Customer' labels do not exist in the schema, ignore the corresponding input count.
    2.  **Infer Other Counts:** For all *other* node labels present in the `schema_analysis`, propose reasonable integer counts based on:
        *   **Exclusion Rule:** If a node label starts with "Derived_", you MUST NOT generate a count for it. Exclude it from the output JSON.
        *   **Relationships:** Analyze the relationships defined in `schema_analysis`. For example:
            *   `(Customer)-[:PLACED_ORDER]->(Order)` suggests generating more `Order` nodes than `Customer` nodes (e.g., 3-10x `num_customers`).
            *   `(Order)-[:CONTAINS]->(Product)` suggests `Product` count might be influenced by `Order` count, but Products likely exist independently too. Consider a reasonable base number of Products (e.g., 50-500) plus potentially more based on order volume.
            *   `(Store)-[:HAS_INVENTORY]->(Product)` suggests inventory links, but doesn't strictly dictate counts. Inventory nodes might be `num_stores * num_products * stocking_factor`.
            *   `(Product)-[:PART_OF_CATEGORY]->(Category)` implies fewer `Category` nodes than `Product` nodes. Hierarchy levels (Department, Category, SubCategory) should generally decrease in count as you go up.
            *   `(Supplier)-[:SUPPLIES]->(Product)` suggests fewer `Supplier` nodes than `Product` nodes.
        *   **Retail Domain Knowledge:** Apply common sense retail ratios. There are usually many products, many customers placing multiple orders, fewer stores than customers, fewer suppliers than products, etc.
        *   **Base Entities:** Identify core entities (like Product, Supplier, maybe Brand, Category) and assign reasonable base counts (e.g., 20-100) if they aren't directly derivable from Customer/Store counts.
        *   **Event Nodes:** Event nodes (like `OrderPlacedEvent`, `PaymentProcessedEvent`) often correspond 1:1 or N:1 with core entities (like `Order`). Generate counts accordingly.
    3.  **Ensure Completeness (for non-excluded nodes):** Provide a count for EVERY node label identified in the `schema_analysis` *that does not start with "Derived_"*. If a count cannot be reasonably inferred for such a node, assign a small default (e.g., 5 or 10).
    4.  **Integer Counts:** Ensure all final counts are non-negative integers.

    Output the final plan as a JSON object where keys are node labels (including 'Store' and 'Customer') and values are the proposed integer counts.
    CRITICAL: You MUST return ONLY the raw, valid JSON object string. Do not include ```json ``` markers, explanations, or any other text.
    """,
    markdown=False, # Expecting raw JSON
)

# 3. Value List Generator
value_list_generator = Agent(
    name="ValueListGenerator",
    model=None, # Placeholder
    instructions=dedent("""\
        You are an expert data generator specializing in RETAIL data and generating **perfectly formatted JSON output**.
        Your task is to generate realistic sample values for STRING properties based on a Neo4j schema analysis and potentially additional context.

        Input: JSON object string {
            "schema_analysis": { /* ... */ },
            "additional_instructions": "/* ... */",
            "property_grouping_definitions": { /* Optional. Content of the property_grouping_definitions.json file.
                For each Node Label (e.g., "Product"):
                - You can define a "_value_groups_config_" key. Its value is a dictionary where:
                    - Keys are property names that should be grouped together.
                    - Values are either "_GENERATE_" (to generate a single value for each instance in the group)
                      or a list of predefined strings (one will be chosen for each instance in the group).
                - You can also define individual properties directly under the Node Label key (e.g., "Product": { "material": ["Cotton", "Wool"] }).
                  Their values MUST be a list of predefined strings. For these, the entire pre-defined list should be assigned as value. These properties will NOT be part of "_value_groups_config_".
                - Any STRING property in the schema (excluding the ID property) that is NOT listed in "_value_groups_config_"
                  AND NOT defined as an individual property with a predefined list, will have a list of 20-30 sample values
                  GENERATED by default.

                Example:
                "Product": {
                  "_value_groups_config_": { "productName": "_GENERATE_", "brandName": ["BrandX", "BrandY"] }, // Inside "_value_groups_config_". Generate a single value for "productName" and choose one from the list for "brandName".
                  "material": ["Cotton", "Polyester"] // Predefined list for an individual property. All the values in the list will be chosen. Not in _value_groups_config_.
                },
                "Customer": { // No _value_groups_config_, just individual property definitions
                  "occupation": ["Engineer", "Doctor"] // Predefined list. All values in the list will be chosen.
                }
            */
            }
        }
        When generating values, adhere to the following specific constraints if applicable:
        - For 'Store.storeName', the generated value should have a maximum of three words.
        - For 'Store.storeLocation', the generated value should have a maximum of two words.
        - For 'Supplier.supplierName', the generated value should have a maximum of three words.
        - For 'Supplier.supplierLocation', the generated value should have a maximum of two words.
        - For 'Supplier.supplierRegion', the generated value should have a maximum of two words.
        - For 'Category.categoryName', the generated value should have a maximum of three words.
        Generate all values in English only. Do not use any other language.                       
                        
        Your Task is to generate a single JSON object:
        1.  **Context is Key:** Use `additional_instructions` (e.g., "apparel retailer") to tailor all generated values.
        2.  **Iterate Schema Properties:** For each `node_label` in `schema_analysis['nodes']`:
            a.  Initialize `output_for_this_label = {}`.
            b.  Let `grouping_defs_for_label = property_grouping_definitions.get(node_label, {})`.
            c.  `properties_handled_in_value_groups = set()`.

            d.  **Process Value Groups (Explicit or Inferred):**
                i.  **Check for Explicit `_value_groups_config_`:**
                    If `"_value_groups_config_"` is a key in `grouping_defs_for_label` and isinstance(grouping_defs_for_label["_value_groups_config_"], dict) and grouping_defs_for_label["_value_groups_config_"]:
                        // This is the existing logic for explicitly defined groups
                        status_message = f"Processing explicit _value_groups_config_ for {node_label}..."
                        Let `value_group_config = grouping_defs_for_label["_value_groups_config_"]`.
                        Create a list of **20-30 dictionaries**. Each dictionary represents one consistent set of values for all properties in `value_group_config`.
                            For each `prop_name_in_group`, `definition` in `value_group_config.items()`:
                                In each of the 20-30 dictionaries:
                                    If `definition` is `"_GENERATE_"`: Generate a single realistic string value for `prop_name_in_group`. Ensure contextual relevance with other values in the *same dictionary set*. Adhere to word count constraints if specified.
                                    If `definition` is a list of strings: Select one value from this predefined list for `prop_name_in_group`. When selecting, ensure it is contextually relevant to the other properties in the same dictionary set.
                                    Store this single value in the current dictionary under the key `prop_name_in_group`.
                                Add `prop_name_in_group` to `properties_handled_in_value_groups`.
                        Store this list of dictionaries as `output_for_this_label["_value_groups_"]`.
                ii. **Else (No Explicit Group Configured), Try to Infer ONE Semantic Group:**
                    Else (if `"_value_groups_config_"` was NOT found in `grouping_defs_for_label` or was empty):
                        status_message = f"No explicit _value_groups_config_ for {node_label}. Attempting to infer one semantic group..."
                        Analyze the STRING properties of the current `node_label` (from `schema_analysis['nodes'][node_label]['properties']`, excluding its `id_property` and any properties already in `properties_handled_in_value_groups`).
                        Attempt to identify **ONE primary semantic group** of 2-3 properties that are very commonly defined together and benefit from consistent co-generation (e.g., `productName` and `brandName`; or `firstName` and `lastName`; or `addressLine1`, `city`, `postalCode`). Prioritize obvious, common retail patterns.
                        If such a strong candidate group is identified (let's call the list of property names `inferred_group_properties`):
                            *   Create a list of **10-15 dictionaries** (fewer than for explicit groups, reflecting the inferential nature).
                                *   For each `prop_name_in_inferred_group` in `inferred_group_properties`:
                                    *   In each of the 10-15 dictionaries: Generate a single realistic string value for `prop_name_in_inferred_group`. Ensure contextual relevance with other values in the *same dictionary set*. Adhere to word count constraints if specified.
                                    *   Store this single value in the current dictionary under the key `prop_name_in_inferred_group`.
                                *   Add `prop_name_in_inferred_group` to `properties_handled_in_value_groups`.
                            *   Store this list of dictionaries as `output_for_this_label["_value_groups_"]`.
                            *   status_message = f"Inferred one semantic group for {node_label} with properties: {', '.join(inferred_group_properties)}."
                        Else:
                            status_message = f"Could not confidently infer a primary semantic group for {node_label}."

            e.  **Process Other String Properties (from Schema):**
                // This part of the logic remains largely the same.
                // It will correctly skip properties that were handled either by an explicit or an inferred group.
                i.  Iterate through all STRING properties (`schema_prop_name`, `schema_prop_type`) for the current `node_label` from `schema_analysis['nodes'][node_label]['properties']`.
                    *   Let `value_group_config = grouping_defs_for_label["_value_groups_config_"]`.
                ii. **Exclude ID Property:** If `schema_prop_name` is the `id_property` for `node_label`, skip it.
                iii. **Exclude Properties Already in `_value_groups_`:** If `schema_prop_name` is in `properties_handled_in_value_groups`, skip it.

                iv. **Determine Source of Values for Remaining Properties:**
                    *   **Case 1: Individual Predefined List from `property_grouping_definitions` (assigning the entire list):**
                        If `schema_prop_name` is a key in `grouping_defs_for_label` (and not `_value_groups_config_`) AND `grouping_defs_for_label[schema_prop_name]` is a list of strings:
                            *   Use the **entire predefined list** `grouping_defs_for_label[schema_prop_name]` as the value.
                            *   Store this list: `output_for_this_label[schema_prop_name] = grouping_defs_for_label[schema_prop_name]`.
                    *   **Case 2: Default Generation (List of 20-30 values):**
                        Else (if `schema_prop_name` was not in `_value_groups_` and not individually predefined):
                            *   Generate a list of **20-30 diverse and realistic sample values** for `schema_prop_name`, considering the node label and domain context. Adhere to word count constraints if specified.
                            *   Store it in `output_for_this_label[schema_prop_name]`.
            f. If `output_for_this_label` is not empty, add it to `output_json[node_label] = output_for_this_label`.

        4.  Structure the output as a single JSON object where:
            *   Top-level keys are the node labels.
            *   Each node label key maps to another JSON object.
            *   This inner object will contain:
                *   Optionally, a `"_value_groups_"` key with its list of dictionaries, if `_value_groups_config_` was defined for that node label in `property_grouping_definitions`.
                *   For string properties individually predefined via `property_grouping_definitions` (Case 1 above), their names as keys, and the **entire predefined list** as their value.
                *   For string properties generated by default (Case 2 above), their names as keys, and their lists of 20-30 sample strings as values.

        Example Output Format (reflecting `property_grouping_definitions` and default generation):
        ```json
        {
          "Product": {
            "_value_groups_": [  // Generated if "Product" has "_value_groups_config_" in property_grouping_definitions
              {
                "productName": "Stylish Hoodie", // Value from "_GENERATE_" or picked from list in _value_groups_config_
                "brandName": "UrbanWear Co."     // Value from "_GENERATE_" or picked from list in _value_groups_config_
              },
              {
                "productName": "Vintage Jeans",
                "brandName": "Retro Threads"
              }
              // ... (typically 20-30 such dictionaries if _value_groups_config_ exists)
            ],
            "material": ["Cotton", "Polyester"], // Example: All values in the list chosen if "Product.material" was ["Cotton", "Polyester"] in property_grouping_definitions
            "productStatus": ["New", "Used", "Refurbished"], // Example: All values in the list chosen if "Product.productStatus" was ["New", "Used", "Refurbished"] in property_grouping_definitions
            "color": ["Red", "Blue", "Green", "Black", "White", "... (up to 30 values)"], // Default generation: list of 20-30 values
            "description": ["High-quality fabric...", "Comfortable fit...", "... (up to 30 values)"] // Default generation
          },
          "Customer": { // Example: If Customer has no "_value_groups_config_"
            "occupation":  ["Engineer", "Doctor"], // Example: All values in the list chosen if "Customer.occupation" was ["Engineer", "Doctor"] in property_grouping_definitions
            "loyaltyTier": ["Gold", "Silver", "Bronze", "... (up to 30 values)"], // Default generation
            "preferredContactMethod": ["Email", "Phone"] // Example: All values in the list chosen if "Customer.preferredContactMethod" was ["Email", "Phone"] in property_grouping_definitions
          },
          "Store": { // Example: If Store only has properties in "_value_groups_config_"
             "_value_groups_": [
               {
                 "storeName": "Downtown Flagship Store", // Max 3 words
                 "storeLocation": "Main Street"      // Max 2 words
               }
               // ... (typically 20-30 such dictionaries)
             ]
          },
          // ... other labels ...
        }
        ```

        CRITICAL: Return ONLY the raw, valid JSON object string. Do not include ```json ``` markers, explanations, or any other text. If a node has no non-ID string properties, omit it from the output JSON.
        """),
    markdown=False, # Expecting raw JSON
)

# 4. Generation Rule Generator 
generation_rule_generator = Agent(
    name="GenerationRuleGenerator",
    model=None, # Placeholder
    instructions=dedent(f"""\
        You are an expert data modeler and data generation specialist for RETAIL systems.
        Your task is to define generation rules for non-string, non-ID properties based on a Neo4j schema analysis.

        Input: JSON object string {{ "schema_analysis": {{...}}, "additional_instructions": "..." }}
        The `schema_analysis` contains 'nodes' (with 'properties' and 'id_property') and 'relationships'.

        Your Task:
        1.  **Analyze Schema:** Iterate through each node label in `schema_analysis['nodes']`.
        2.  For each node, iterate through its `properties`.
        3.  **Filter Properties:**
            *   **Exclude** the property designated as the `id_property` for that node label.
            *   **Exclude** properties with `type` == "String" (these are handled by value lists).
            *   **Exclude** properties with `type` == "Boolean" (these are typically random true/false).
        4.  **Define Ranges/Rules:** For the remaining properties (Integer, Float, Date, DateTime), define reasonable default generation ranges or rules.
            *   **Consider Property Name:** If the property name gives a strong hint (e.g., 'age', 'price', 'quantity', 'orderDate', 'birthDate'), suggest a specific, realistic range.
            *   **Consider Domain Context:** Use `additional_instructions` (e.g., "apparel retailer") to tailor ranges. For example, prices for apparel might differ from electronics.
            *   **Default Ranges:** Provide sensible default ranges for each data type if no specific property name hint is strong enough.
        5.  **Structure the Output:**
            Output a single JSON object with a top-level key `type_ranges`.
            Under `type_ranges`, create keys for "integer", "float", "date", "datetime".
            Each type key maps to another JSON object:
                *   A "default" key with a list representing the default range (e.g., `[min, max]` for numbers, `["YYYY-MM-DD_start", "YYYY-MM-DD_end"]` for dates).
                *   Additional keys for specific qualified property names (e.g., `NodeLabel.propertyName` or `RelationshipType.propertyName`, preserving original casing from schema) that require a non-default range, with their specific range list.
                    *   For dates/datetimes, ranges can be absolute ISO dates/datetimes or relative (e.g., ["-2Y", "NOW"] for last 2 years up to now). Use "NOW" for current date and "NOW_DATETIME" for current datetime.
                *   When creating keys for specific property rules, construct the qualified name by combining the node label (exact case) and simple property name (exact case) like node_label.simple_property_name"`.
                *   When creating keys for specific property rules, the key should be a string formed by concatenating the current node label (preserving its exact case), a period character (.), and the current simple property name (preserving its exact case). For example, if the current node label is "Customer" and the property name is "age", the key should be the string "Customer.age".

        Example Output Format for `type_ranges` (ensure "property_dependencies" and "derived_properties" are also top-level keys, possibly empty):
        ```json
        {{
          "type_ranges": {{
            "integer": {{ "default": [0, 1000], "Customer.age": [18, 99], "Product.year": [2022, "current_year"] }},
            "float": {{ "default": [0.0, 1000.0], "Product.price": [0.99, 1999.99] }},
            "date": {{ "default": ["2020-01-01", "NOW"], "Customer.birthDate": ["-70Y", "-18Y"] }},
            "datetime": {{ "default": ["2020-01-01T00:00:00", "NOW_DATETIME"] }}
          }},
          "property_dependencies": {{ "_global_": {{ "value_constraints": [], "mutually_exclusive": [], "associative": [] }} }},
          "derived_properties": {{}}
        }}
        ```
        CRITICAL: Return ONLY the raw, valid JSON object string. Do not include ```json ``` markers, explanations, or any other text.
        """),
    markdown=False, # Expecting raw JSON
)

# 5. Python Code Planner
PYTHON_CODE_PLANNER_BASE_INSTRUCTIONS = dedent(f"""\
    You are an expert Python developer designing a data generation script for Neo4j based on configuration files.

    Input: JSON object string {{
        "schema_analysis_filename": "{SCHEMA_ANALYSIS_FILENAME}",
        "generation_plan_filename": "{GENERATION_PLAN_FILENAME}",
        "value_lists_filename": "{VALUE_LISTS_FILENAME}",
        "cardinality_rules_filename": "{CARDINALITY_RULES_FILENAME}", /* Optional */
        "generation_rules_filename": "{GENERATION_RULES_FILENAME}", /* New */
        "enforce_date_consistency": true/false,
        "additional_instructions": "..."
    }}

    Your Task:
    Outline a detailed plan for a Python script (`generate_neo4j_data.py`) that will:
    1.  **Import necessary libraries:** `datetime`, `random`, `os`, `json`, `logging`, `decimal`.
    2.  **Define Configuration:**
        *   Define constants for the input filenames provided in the input message (e.g., `SCHEMA_FILENAME = "{SCHEMA_ANALYSIS_FILENAME}"`).
        *   Include a variable for the output Cypher filename (e.g., `output.cypher`).
        *   Include basic logging setup.
    3.  **Load Configuration Files:**
        *   Plan a function `load_config_data()` that reads the JSON files specified by the filename constants.
        *   Use `os.path.join` to construct paths relative to the script's directory.
        *   Use `json.load()` within `with open(...)` blocks.
        *   Include error handling (`try...except FileNotFoundError, json.JSONDecodeError`) for each file load. Log errors and exit gracefully if essential files (schema, plan, values, generation_rules) are missing/invalid.
        *   Load cardinality rules only if the file exists; default to an empty dictionary `{{}}` if not found or invalid.
        *   Return all loaded data (e.g., `schema_data`, `plan_data`, `value_lists_data`, `cardinality_rules_data`, `generation_rules_data`).
    4.  **Extract Top-Level Config:** After loading, extract node counts from `plan_data` into top-level variables for easy access/modification.
    5.  **Data Storage:** Plan for in-memory dictionaries (`generated_data`) storing generated node IDs.
    6.  **Helper Functions:**
        *   `generate_sequential_id(label, id_property_name, id_property_type, counter)`: Takes the label, its id_property name, its id_property type (from schema_analysis), and the current counter for that label. If type is 'String', returns `f"{{id_property_name}}_{{counter:04d}}"`. Otherwise (e.g., 'Integer'), returns `counter`.
        *   `format_cypher_value(value)`: Plan to handle `Decimal`.
        *   `format_cypher_properties(props_dict)`
        *   `write_cypher(file_handle, statement)`
        *   `generate_property_value(label_or_rel_type, qualified_prop_name, prop_type, value_lists_data, generation_rules_data, context_props=None)`:
            *   `qualified_prop_name` will be in the format `NodeLabel.propertyName` (e.g., `Customer.age`) or `RelationshipType.propertyName`.
            *   For "String" `prop_type` (non-ID):
                *   Extract `simple_prop_name` from `qualified_prop_name` (e.g., "age" from "Customer.age").
                *   Use `value_lists_data[label_or_rel_type][simple_prop_name]` to get the list of values.
            *   For other `prop_type` (Integer, Float, Date, DateTime):
                *   Look up rules in `generation_rules_data['type_ranges'][prop_type_lower]`.
                *   Attempt to find a specific rule using `qualified_prop_name` as the key (e.g., `rules_for_type.get(qualified_prop_name)`).
                *   If a specific rule for `qualified_prop_name` is not found, use the `rules_for_type.get('default')` rule for that `prop_type`.
            *   Handle date consistency if `enforce_date_consistency` is true.
            *   This function should NOT be used for the node's `id_property`.
        *   Optional date helper functions if needed for consistency.
    7.  **Node Generation Logic:**
        *   Iterate `plan_data` (loaded node counts).
        *   For each label, maintain a counter (e.g., in a dictionary `node_counters = {{label: 1}}`).
        *   Inside the loop for each node instance:
            *   Initialize an empty dictionary for `node_props`.
            *   Fetch `id_property_name` and `id_property_type` for the current label from `schema_analysis`.
            *   Generate the ID property value using `generate_sequential_id(label, id_property_name, id_property_type, node_counters[label])` and add it to `node_props`. Increment `node_counters[label]`.
            *   Iterate through *other* properties (excluding the `id_property`) defined in `schema_analysis`.
            *   Construct `qualified_prop_name` as `f"{{label}}.{{simple_prop_name}}"`.
            *   Call `generate_property_value(label, qualified_prop_name, prop_type, ...)` for these other properties. # Corrected placeholder
        *   Store the completed `node_props` dictionary (including the generated ID) in `generated_data["nodes_for_cypher"]`.
        *   Store the generated ID value itself (not the whole props dict) in `generated_data["nodes"][label]` for relationship linking.
    8.  **Relationship Generation Logic:**
        *   Iterate through `schema_data['relationships']`.
        *   Identify `source_label`, `target_label`, `rel_type`.
        *   **Determine Number and Strategy:**
            *   Check if a rule exists for this relationship in the loaded `cardinality_rules_data`.
            *   **If a rule exists:** Plan to iterate through each generated source node. Determine #rels using `random.randint(rule['min'], rule['max'])`. Select targets randomly (`random.sample`).
            *   **If NO rule exists (Hybrid Default):**
                *   Get `source_count = plan_data[source_label]` and `target_count = plan_data[target_label]`.
                *   Calculate `num_rels_to_create = min(source_count, target_count)`.
                *   Identify smaller/larger node ID sets from `generated_data`.
                *   Plan to iterate through each node ID in the *smaller* set.
                *   For each, randomly select *one unique* node ID from the *larger* set (track used nodes from larger set).
        *   **Selection & Consistency:** Ensure target selection respects `enforce_date_consistency` if applicable. # Corrected placeholder
        *   Generate relationship properties: For each property, get its simple `prop_name` and `prop_type`. Construct `qualified_prop_name` as `f"{{rel_type}}.{{simple_prop_name}}"`.
            Call `generate_property_value(rel_type, qualified_prop_name, prop_type, ...)` passing context, `value_lists_data`, and `generation_rules_data`.
        *   Store relationship details.
    9.  **Cypher Generation Logic:** Plan file opening, `UNWIND` for nodes, `MATCH`/`CREATE` or `MATCH`/`MERGE` for relationships, index comments, basic error handling.
    10. **Main Execution Block:** Call `load_config_data()`, orchestrate generation steps, handle file writing.
    11. **Apply Additional Instructions:** Incorporate user instructions.
    12. **Dependencies:** Add comment listing required packages.

    Output: A detailed, step-by-step plan in **Markdown format**. Emphasize reading files and the hybrid default cardinality.
    """)
python_code_planner = Agent(
    name="PythonCodePlanner",
    model=None, # Placeholder
    instructions=PYTHON_CODE_PLANNER_BASE_INSTRUCTIONS, # Use the variable
    markdown=True, # Expecting Markdown output
)

# 6. Python Code Generator
python_code_generator = Agent(
    name="PythonCodeGenerator",
    model=None, # Placeholder
    instructions=dedent(f"""\
        You are an expert Python programmer specializing in Neo4j data generation for RETAIL scenarios using configuration files.
        You will receive:
        1.  Input Filenames (JSON string): {{ "schema_analysis_filename": "{SCHEMA_ANALYSIS_FILENAME}", ... }}
        2.  Date Consistency Flag (Boolean).
        3.  A detailed Code Plan (Markdown string) outlining the script structure.
        (The script will load schema_analysis.json, generation_plan.json, value_lists.json, generation_rules.json, and optionally cardinality_rules.json)

        Your Task:
        Write a complete and functional Python script (`generate_neo4j_data.py`) based *exactly* on the provided Code Plan.

        **CRITICAL REQUIREMENTS:**
        1.  **Adhere to Code Plan:** Implement all steps, functions, and logic described in the Markdown Code Plan.
        2.  **Configuration Loading:**
            *   Define constants for input filenames (`SCHEMA_FILENAME`, `PLAN_FILENAME`, etc.).
            *   Implement `load_config_data()` to read all JSON files (schema, plan, values, generation_rules, optional cardinality) with error handling.
            *   Extract node counts from loaded plan data into top-level variables. Ensure robust parsing for all loaded JSON files.
        3.  **Implement `generate_property_value(label_or_rel_type, qualified_prop_name, prop_type, value_lists_data, generation_rules_data, context_props=None)` Function:**
            *   The `qualified_prop_name` argument will be in the format `NodeLabel.propertyName` or `RelationshipType.propertyName`.
            *   For "String" `prop_type` (non-ID):
                *   Extract the `simple_prop_name` from `qualified_prop_name` (e.g., "age" from "Customer.age").
                value = value_lists_data.get(label_or_rel_type, {{}}).get(simple_prop_name, [])
            *   For other `prop_type` (Integer, Float, Date, DateTime):
                *   Look up rules in `generation_rules_data.get('type_ranges', {{}}).get(prop_type.lower(), {{}})`.
                *   First, try to get the specific rule using `qualified_prop_name` as the key (e.g., `rules_for_type.get(qualified_prop_name)`).
                *   If a specific rule for `qualified_prop_name` is not found, use the `rules_for_type.get('default')` rule.
                *   Parse and apply the range from the selected rule. Implement logic to handle "NOW", "current_year", and relative date strings (e.g., "-2Y").
            *   Handle date consistency.
        4.  **Node Generation Loop:**
            *   Maintain a counter for each node label (e.g., `node_counters = {{label: 1}}`).
            *   For each node instance, retrieve its `id_property_name` and `id_property_type` from the loaded `schema_analysis`.
            *   Generate the ID: if `id_property_type` is 'String', use `f"{{id_property_name}}_{{node_counters[label]:04d}}"`. Otherwise (e.g., 'Integer'), use `node_counters[label]`. Increment the counter.
            *   For other properties (not the ID property), construct `qualified_prop_name` (e.g., `f"{{label}}.{{prop_name}}"`) and call `generate_property_value`.
            *   Store the generated node ID (the actual value, not the property name) in `generated_data["nodes"][label]` and the full `node_props` in `generated_data["nodes_for_cypher"]`.
        5.  **Relationship Generation:**
            *   Retrieve generated IDs from `generated_data`.
            *   **Check loaded `cardinality_rules_data`:**
                *   **If rule exists:** Implement the per-source random selection logic (`random.randint`, `random.sample`).
                *   **If NO rule exists (Implement Hybrid Default):** Implement the `min(source_count, target_count)` logic, iterating through the smaller set and pairing uniquely with the larger set. Handle empty node lists.
            *   Apply date consistency checks if `enforce_date_consistency` is true. # Corrected placeholder
            *   For relationship properties, construct `qualified_prop_name` (e.g., `f"{{rel_type}}.{{prop_name}}"`) and call `generate_property_value`.
        6.  **Cypher Output:** Use `UNWIND` for nodes. Use `MATCH`/`CREATE` or `MATCH`/`MERGE` for relationships. Handle `Decimal`. Include index comments. Write to the configured Cypher filename.
        7.  **Error Handling:** Implement `try...except` for file I/O and configuration loading.
        8.  **Dependencies:** Include comment listing required packages (`# Required: pip install ...`).
        9.  **Modularity & Comments:** Use functions and comments.

        **Output Format:**
        Return ONLY the complete Python script enclosed in a single markdown code block (```python ... ```).
        """),
    markdown=False, # Expecting raw code block
)

# --- Streamlit UI ---
st.set_page_config(layout="wide", page_title="Neo4j DataGen Code Wizard")
st.title("🧙‍♂️ Neo4j Synthetic Data Code Wizard")
st.caption("Upload a Neo4j schema, configure settings, generate realistic value lists, review/edit them, and generate a Python script to create synthetic data.")
st.divider()

# --- Initialize Session State ---
if 'value_lists_generated' not in st.session_state:
    st.session_state.value_lists_generated = None
if 'value_lists_edited' not in st.session_state:
    st.session_state.value_lists_edited = None
if 'value_lists_confirmed' not in st.session_state:
    st.session_state.value_lists_confirmed = False
if 'final_script_generated' not in st.session_state:
    st.session_state.final_script_generated = None
if 'generation_rules_generated' not in st.session_state: # New state for generation rules
    st.session_state.generation_rules_generated = None
if 'cardinality_rules_saved' not in st.session_state:
    st.session_state.cardinality_rules_saved = False # Track if rules were saved
if 'data_plan_source' not in st.session_state:
    st.session_state.data_plan_source = 'Generate' # Default to generating data plan
if 'generation_plan_generated' not in st.session_state:
    st.session_state.generation_plan_generated = None
if 'value_list_source' not in st.session_state:
    st.session_state.value_list_source = 'Generate' # Default to generating
if 'property_grouping_definitions_content' not in st.session_state:
    st.session_state.property_grouping_definitions_content = None

# --- Sidebar for Configuration ---
with st.sidebar:
    st.header("⚙️ Configuration")
    st.divider()
    # --- Model Selection ---
    st.subheader("🤖 AI Model")
    available_models = get_available_gemini_models()
    default_model_name = "models/gemini-1.5-pro-latest"
    if available_models:
        try: default_index = available_models.index(default_model_name) if default_model_name in available_models else 0
        except ValueError: default_index = 0; logger.warning(f"Default model '{default_model_name}' not found.")
        selected_model_name = st.selectbox("Select Gemini Model:", available_models, index=default_index, help="Choose the AI model for generation.")
    else:
        st.warning("Could not retrieve models."); selected_model_name = default_model_name; st.caption(f"Using fallback: {selected_model_name}")
    st.divider()

    # --- Schema Analysis Step ---
    st.subheader("📊 Schema Analysis Step")
    run_schema_analysis = st.checkbox(
        "Run Schema Analyzer Agent?",
        value=True, # Default to running the analysis
        help="Check this if your input schema needs analysis to identify node properties, types, and ID properties. Uncheck if your input schema JSON already contains this analysis structure (including 'id_property')."
    )
    st.divider()

    # --- Schema Upload ---
    st.subheader("📄 Schema Input")
    upload_help_text = (
        "Upload the raw Neo4j schema JSON (may be missing 'id_property'). The Schema Analyzer agent will process it."
        if run_schema_analysis else
        f"Upload the pre-analyzed schema JSON (must match structure of '{SCHEMA_ANALYSIS_FILENAME}' including 'id_property')."
    )
    uploaded_schema_file = st.file_uploader(
        "Upload Schema JSON",
        type=["json"],
        label_visibility="collapsed",
        help=upload_help_text,
        key="schema_uploader" # Add key to differentiate
    )
    st.divider()

    # --- Value List Source Selection ---
    st.subheader("📝 Value Lists Source")
    st.session_state.value_list_source = st.radio(
        "Choose Value List Source:",
        ('Generate', 'Upload'),
        index=0 if st.session_state.value_list_source == 'Generate' else 1, # Maintain selection across reruns
        horizontal=True,
        label_visibility="collapsed",
        help=f"Choose 'Generate' to use the AI agent or 'Upload' to provide a pre-existing '{VALUE_LISTS_FILENAME}' file."
    )

    uploaded_value_list_file = None
    if st.session_state.value_list_source == 'Upload':
        uploaded_value_list_file = st.file_uploader(
            f"Upload Value Lists JSON ({VALUE_LISTS_FILENAME})",
            type=["json"],
            label_visibility="visible", # Show label when upload is selected
            help=f"Upload a JSON file containing pre-generated value lists, matching the expected structure.",
            key="value_list_uploader" # Add key
        )
    st.divider()

    # --- Generation Plan Source Selection ---
    st.subheader("📈 Generation Plan Source")
    st.session_state.data_plan_source = st.radio(
        "Choose Generation Plan Source:",
        ('Generate', 'Upload'),
        index=0 if st.session_state.data_plan_source == 'Generate' else 1,
        horizontal=True,
        label_visibility="collapsed",
        help=f"Choose 'Generate' to use the AI agent or 'Upload' to provide a pre-existing '{GENERATION_PLAN_FILENAME}' file."
    )

    uploaded_generation_plan_file = None
    if st.session_state.data_plan_source == 'Upload':
        uploaded_generation_plan_file = st.file_uploader(
            f"Upload Generation Plan JSON ({GENERATION_PLAN_FILENAME})",
            type=["json"],
            help=f"Upload a JSON file containing a pre-defined generation plan.",
            key="generation_plan_uploader")
    st.divider()

    # --- Generation Parameters ---
    st.subheader("📊 Data Scale")
    col1, col2 = st.columns(2)
    with col1: num_stores = st.number_input("Target Stores", min_value=1, max_value=10000, value=50, step=10)
    with col2: num_customers = st.number_input("Target Customers", min_value=1, max_value=100000, value=1000, step=100)
    st.divider()

    # --- Relationship & Data Config ---
    st.subheader("🔗 Relationship & Data Config")
    cardinality_rules_json = st.text_area(
        "Define Relationship Cardinality (JSON, Optional):",
        placeholder='{\n  "Customer_PLACED_ORDER_Order": {"min": 1, "max": 10},\n  "Order_CONTAINS_Product": {"min": 1, "max": 5}\n}',
        height=150,
        help=f"Specify min/max relationships per source node (Key: 'SourceLabel_REL_TYPE_TargetLabel'). "
             f"If NOT provided, the script will generate min(source_count, target_count) relationships "
             f"by pairing each node from the smaller set with a unique random node from the larger set. "
             f"Saved to '{CARDINALITY_RULES_FILENAME}' if provided."
    )
    enforce_date_consistency = st.checkbox(
        "Attempt to enforce date consistency?", value=True,
        help="Instruct agents to plan for logical date ordering (e.g., shipDate after orderDate). May increase complexity."
    )
    uploaded_property_grouping_file = st.file_uploader(
        "Upload Property Grouping Definitions (JSON, Optional):",
        type=["json"],
        help=(
            "Optionally, upload a JSON file to define property groupings and pre-defined values for the Value List Generator. "
            "Refer to the ValueListGenerator agent's instructions for the expected format. "
            "If not provided, all string values (except IDs) will be generated by default."
        ),
        key="prop_group_uploader"
    )
    if uploaded_property_grouping_file:
        st.session_state.property_grouping_definitions_content = uploaded_property_grouping_file.read().decode("utf-8")
    st.divider()

    # --- Additional Instructions ---
    st.subheader("📝 Additional Instructions (Context)")
    additional_planner_instructions = st.text_area(
        "Provide context for data generation (e.g., domain like 'apparel retailer', specific style):",
        placeholder="e.g., Generate data for an apparel retailer focusing on sportswear, Use UK addresses and currency...",
        height=100,
        help="This context will guide the Value List Generator and the Code Planner."
    )
    st.divider()
    # --- Output Settings ---
    st.subheader("💾 Output Files")
    output_py_filename = st.text_input("Python Script Name:", value="generated_datagen_script.py")
    output_cypher_filename = st.text_input("Cypher File Name (in script):", value="generated_data.cypher")
    st.divider()
    # --- Generate Button ---
    # Disable button if required uploads are missing
    disable_generate = (uploaded_schema_file is None) or \
                       (st.session_state.value_list_source == 'Upload' and uploaded_value_list_file is None) or \
                       (st.session_state.data_plan_source == 'Upload' and uploaded_generation_plan_file is None)
    generate_button = st.button("🚀 Start Generation Workflow", type="primary", use_container_width=True, disabled=disable_generate)
    if uploaded_schema_file is None: st.warning("Please upload a schema file.")
    if st.session_state.data_plan_source == 'Upload' and uploaded_generation_plan_file is None: st.warning(f"Please upload a Generation Plan JSON file or choose 'Generate'.")
    if st.session_state.value_list_source == 'Upload' and uploaded_value_list_file is None: st.warning("Please upload a Value Lists JSON file or choose 'Generate'.")


# --- Main Area ---
generation_rules_content_string = None # Initialize
schema_content_string = None
if uploaded_schema_file is not None:
    try:
        uploaded_schema_file.seek(0)
        schema_content_string = uploaded_schema_file.read().decode("utf-8")
        # Display Schema Summary (optional but good)
        with st.expander("Uploaded Schema Summary", expanded=False):
            try:
                # Try parsing as raw schema OR analyzed schema for summary
                schema_data_for_summary = json.loads(schema_content_string)
                nodes_info = schema_data_for_summary.get("nodes", {})
                rels_info = schema_data_for_summary.get("relationships", {})
                st.metric("Node Labels Found", len(nodes_info))
                st.metric("Relationship Types Found", len(rels_info) if isinstance(rels_info, (list, dict)) else 0)
            except Exception as summary_e:
                st.warning(f"Could not display schema summary: {summary_e}")
    except Exception as e:
        st.error(f"❌ Error reading/parsing schema file: {e}")
        schema_content_string = None

# --- Define Tabs Earlier ---
tab_schema, tab_plan, tab_values, tab_gen_rules, tab_code_plan, tab_final_code = st.tabs([
    "📊 Schema Analysis",
    "📈 Generation Plan",
    "📝 Value Lists", # Simplified name
    "🔢 Generation Rules", # New Tab
    "📋 Python Code Plan",
    "🐍 Generated Script"
])

# --- Agent Execution Workflow ---
if generate_button and schema_content_string:
    st.session_state.value_lists_generated = None # Reset state on new run
    st.session_state.value_lists_edited = None
    st.session_state.value_lists_confirmed = False
    st.session_state.final_script_generated = None
    st.session_state.generation_rules_generated = None # Reset new state
    st.session_state.generation_plan_generated = None # Reset generation plan
    st.session_state.cardinality_rules_saved = False # Reset flag
    # st.session_state.property_grouping_definitions_content is already handled by the uploader
    st.session_state.raw_schema_agent_batch_outputs = [] # To store raw outputs from each agent call in batching

    st.info("🚀 Starting agent workflow...")
    # --- Create Agent Output Directory ---
    script_dir = os.path.dirname(os.path.abspath(__file__))
    agent_output_path = os.path.join(script_dir, AGENT_OUTPUT_DIR)
    try: os.makedirs(agent_output_path, exist_ok=True); logger.info(f"Agent output directory: {agent_output_path}")
    except OSError as e: st.error(f"Could not create agent output directory: {e}"); st.stop()

    schema_analysis_dict = None
    generation_plan_dict = None
    value_lists_dict = None
    generation_rules_dict = None # For the new rules
    cardinality_rules_dict = {} # Initialize as empty
    property_grouping_definitions_dict = {} # Initialize for property groupings
    python_code_plan_md = None
    final_python_script = None

    # --- Use st.status for detailed progress ---
    with st.status("Running Agent Workflow...", expanded=True) as status:
        try:
            # --- Setup ---
            status.write("🔧 Initializing AI Model...")
            google_api_key = os.getenv("GOOGLE_API_KEY") or st.secrets.get("GOOGLE_API_KEY")
            print(f"Google API Key: {google_api_key}") # Debugging line
            if not google_api_key: status.update(label="🚨 Error: Google API key not found.", state="error"); st.stop()
            model_id_for_agno = selected_model_name if selected_model_name.startswith("models/") else f"models/{selected_model_name}"
            agno_gemini_model_wrapper = Gemini(id=model_id_for_agno, api_key=google_api_key)
            logger.info(f"Using Agno Gemini wrapper for model ID: {model_id_for_agno}")
            schema_analyzer.model = agno_gemini_model_wrapper
            data_planner.model = agno_gemini_model_wrapper
            value_list_generator.model = agno_gemini_model_wrapper
            generation_rule_generator.model = agno_gemini_model_wrapper # Assign model to new agent
            python_code_planner.model = agno_gemini_model_wrapper
            python_code_generator.model = agno_gemini_model_wrapper
            status.write("✅ AI Model Initialized.")

            # Initialize schema_stream_placeholder outside the conditional block
            # so it's available if parsing uploaded schema fails.
            schema_stream_placeholder = None

            # --- Conditional Schema Analysis ---
            if run_schema_analysis:
                status.write("📊 Running Schema Analyzer Agent...")
                logger.info("SchemaAnalyzer: Processing in batches...")

                try:
                    full_input_schema_dict = json.loads(schema_content_string)
                except json.JSONDecodeError as e:
                    status.update(label=f"🚨 Schema Input Error: Invalid JSON in uploaded schema. {e}", state="error", expanded=True)
                    with tab_schema:
                        st.error(f"The uploaded schema file contains invalid JSON and cannot be parsed: {e}")
                        st.code(schema_content_string, language="json")
                    st.stop()

                input_nodes_map = full_input_schema_dict.get("nodes", {})
                raw_relationships = full_input_schema_dict.get("relationships", {})
                input_relationships_list = []

                if isinstance(raw_relationships, dict):
                    for rel_type, rel_data_val in raw_relationships.items():
                        if isinstance(rel_data_val, list): # Format like OntoV7.4.6.ttl output
                            for rel_instance in rel_data_val:
                                input_relationships_list.append({
                                    "type": rel_type,
                                    "source": rel_instance.get("start_node_label"),
                                    "target": rel_instance.get("end_node_label"),
                                    "properties": rel_instance.get("properties", []) # Keep existing properties
                                })
                        elif isinstance(rel_data_val, dict): # Format like OntoV7.4.5.ttl output
                             # Extract properties if they exist at this level
                            rel_props = []
                            if "properties" in rel_data_val and isinstance(rel_data_val["properties"], list):
                                rel_props = rel_data_val["properties"]
                            elif "properties" in rel_data_val and isinstance(rel_data_val["properties"], dict): # if properties is a dict
                                rel_props = [{"name": k, "type": v.get("type", "String")} for k,v in rel_data_val["properties"].items()]

                            input_relationships_list.append({
                                "type": rel_type,
                                "source": rel_data_val.get("start_node_labels")[0] if rel_data_val.get("start_node_labels") is not None and len(rel_data_val.get("start_node_labels")) > 0 else None,
                                "target": rel_data_val.get("end_node_labels")[0] if rel_data_val.get("end_node_labels") else None,
                                "properties": rel_props
                            })
                elif isinstance(raw_relationships, list): # Already a list of relationship dicts
                    input_relationships_list = raw_relationships

                all_node_labels = list(input_nodes_map.keys())
                NODE_BATCH_SIZE = 15
                RELATIONSHIP_BATCH_SIZE = 15

                final_merged_schema_analysis_nodes = {}
                final_merged_schema_analysis_relationships_map = {} # Stores unique rels by (type,src,tgt) -> rel_data
                # Ensure reset here, before any batching starts
                st.session_state.raw_schema_agent_batch_outputs = [] # Ensure reset here
                
                num_node_batches = math.ceil(len(all_node_labels) / NODE_BATCH_SIZE) if all_node_labels else 0
                num_relationship_batches = math.ceil(len(input_relationships_list) / RELATIONSHIP_BATCH_SIZE) if input_relationships_list else 0
                total_batches = num_node_batches + num_relationship_batches

                if total_batches == 0:
                    logger.info("Input schema (nodes and relationships) is empty. SchemaAnalyzer will not run.")
                    schema_analysis_dict = {"nodes": {}, "relationships": []}
                    status.write("✅ Schema Analysis Complete (Input was empty).")
                    with tab_schema: st.json(schema_analysis_dict)
                else:
                    with tab_schema: 
                        st.info(f"Schema Analyzer processing in {total_batches} batch(es) (Nodes first, then Relationships)...")
                        batch_status_placeholder = st.empty() # For messages like "Batch X/Y streaming..."
                        batch_content_placeholder = st.empty() # For the streaming code content

                    for i in range(total_batches):
                        batch_nodes_for_agent = {}
                        current_node_labels_batch_display = [] # For logging
                        batch_relationships_for_agent_final = []
                        log_msg_nodes = "N/A"
                        log_msg_rels = "N/A"

                        if i < num_node_batches: # This is a node-processing batch
                            start_idx = i * NODE_BATCH_SIZE
                            end_idx = start_idx + NODE_BATCH_SIZE
                            current_node_labels_batch_display = all_node_labels[start_idx:end_idx]
                            batch_nodes_for_agent = {label: input_nodes_map[label] for label in current_node_labels_batch_display}
                            # batch_relationships_for_agent_final remains empty for node batches
                            log_msg_nodes = f"nodes: {current_node_labels_batch_display or 'N/A'}"
                            log_msg_rels = "relationships: N/A (Node batch)"

                        elif (i - num_node_batches) < num_relationship_batches: # This is a relationship-processing batch
                            # batch_nodes_for_agent remains empty for relationship batches
                            relationship_batch_index = i - num_node_batches
                            start_rel_idx = relationship_batch_index * RELATIONSHIP_BATCH_SIZE
                            end_rel_idx = start_rel_idx + RELATIONSHIP_BATCH_SIZE
                            batch_relationships_for_agent_final = input_relationships_list[start_rel_idx:end_rel_idx]
                            log_msg_nodes = "nodes: N/A (Relationship batch)"
                            log_msg_rels = f"relationships: {len(batch_relationships_for_agent_final)} items"
                        
                        else: # Should not be reached if total_batches is calculated correctly
                            logger.warning(f"SchemaAnalyzer: Batch {i+1}/{total_batches} is unexpectedly empty or out of bounds. Skipping.")
                            batch_status_placeholder.warning(f"Schema Analyzer: Batch {i+1}/{total_batches} was unexpectedly empty. Skipping.")
                            continue
                        
                        batch_input_dict = {"nodes": batch_nodes_for_agent, "relationships": batch_relationships_for_agent_final}
                        batch_input_json_string = json.dumps(batch_input_dict)

                        batch_status_placeholder.info(f"📊 Schema Analyzer: Batch {i+1}/{total_batches} processing {log_msg_nodes}, {log_msg_rels}...")
                        logger.info(f"SchemaAnalyzer: Running Batch {i+1}/{total_batches} with {log_msg_nodes}, {log_msg_rels}")
                        
                        batch_schema_analysis_json_string = None
                        batch_schema_full_response_content = ""
                        try:
                            batch_stream_iterator = schema_analyzer.run(message=batch_input_json_string, stream=True)
                            for chunk_count, chunk in enumerate(batch_stream_iterator):
                                if chunk and hasattr(chunk, 'content') and chunk.content:
                                    batch_schema_full_response_content += chunk.content
                                    batch_content_placeholder.code(batch_schema_full_response_content, language="json")
                            batch_schema_analysis_json_string = batch_schema_full_response_content
                            st.session_state.raw_schema_agent_batch_outputs.append(batch_schema_analysis_json_string) # Save raw output
                            logger.info(f"SchemaAnalyzer batch {i+1} streaming finished.")

                        except ModelProviderError as mpe:
                            status.update(label=f"🚨 Schema Analyzer Batch {i+1} ModelProviderError: {mpe}", state="error", expanded=True)
                            logger.error(f"ModelProviderError during SchemaAnalyzer batch {i+1}: {mpe}", exc_info=True)
                            batch_status_placeholder.error(f"ModelProviderError during Schema Analysis Batch {i+1}: {mpe}")
                            batch_content_placeholder.code(batch_schema_full_response_content or "No content before error", language="text")
                            st.stop() # Stop on first batch error for simplicity
                        except Exception as run_error:
                            status.update(label=f"🚨 Schema Analyzer Batch {i+1} Run Error: {run_error}", state="error", expanded=True)
                            logger.error(f"Generic error during SchemaAnalyzer batch {i+1}: {run_error}", exc_info=True)
                            batch_status_placeholder.error(f"Error during Schema Analysis Batch {i+1}: {run_error}")
                            batch_content_placeholder.code(batch_schema_full_response_content or "No content before error", language="text")
                            st.stop() # Stop on first batch error

                        batch_analysis_dict = safe_json_loads(batch_schema_analysis_json_string)
                        if batch_analysis_dict is None:
                            status.update(label=f"🚨 Schema Analyzer Batch {i+1} Error: Invalid JSON output.", state="error", expanded=True)
                            logger.error(f"Schema Analyzer Agent batch {i+1} failed to return valid JSON.")
                            batch_status_placeholder.error(f"Schema Analyzer Agent Batch {i+1} failed to return valid JSON after streaming.")
                            batch_content_placeholder.code(batch_schema_analysis_json_string or "No content received from agent for this batch", language="text")
                            # Save this problematic raw output for debugging
                            try:
                                with open(os.path.join(agent_output_path, f"failed_schema_batch_{i+1}_raw_output.json"), "w", encoding="utf-8") as f:
                                    f.write(batch_schema_analysis_json_string or "{}")
                                batch_status_placeholder.info(f"Problematic raw output for batch {i+1} saved.")
                            except Exception as e_save:
                                logger.warning(f"Could not save problematic raw output for batch {i+1}: {e_save}")
                            st.stop() # Stop on first batch error

                        final_merged_schema_analysis_nodes.update(batch_analysis_dict.get("nodes", {}))
                        for rel_data in batch_analysis_dict.get("relationships", []):
                            rel_key = (rel_data.get("type"), rel_data.get("source"), rel_data.get("target"))
                            if all(k is not None for k in rel_key) and rel_key not in final_merged_schema_analysis_relationships_map:
                                final_merged_schema_analysis_relationships_map[rel_key] = rel_data
                        
                        batch_status_placeholder.success(f"Schema Analyzer Batch {i+1}/{total_batches} processed.")
                        batch_content_placeholder.json(batch_analysis_dict) # Show parsed JSON for the batch

                    schema_analysis_dict = {
                        "nodes": final_merged_schema_analysis_nodes,
                        "relationships": list(final_merged_schema_analysis_relationships_map.values())
                    }
                    status.write("✅ Schema Analysis Agent Complete (Batched).")
                    with tab_schema: # Clear batch placeholders and show final
                        batch_status_placeholder.empty()
                        batch_content_placeholder.empty()
                        st.json(schema_analysis_dict)
            else: # Not run_schema_analysis (parse uploaded file directly)
                status.write("📊 Parsing uploaded file as Schema Analysis...")
                logger.info("Parsing uploaded file directly as schema analysis...")
                with tab_schema: schema_placeholder_direct = st.empty() # Placeholder for direct parse
                schema_analysis_dict = safe_json_loads(schema_content_string)
                if schema_analysis_dict is None:
                    status.update(label="🚨 Schema Parsing Error: Invalid JSON in uploaded file.", state="error", expanded=True);
                    with tab_schema: schema_placeholder_direct.error("The uploaded file does not contain valid JSON or the expected schema analysis structure."); st.code(schema_content_string or "No content")
                    st.stop()
                if "nodes" not in schema_analysis_dict or "relationships" not in schema_analysis_dict:
                     status.update(label="🚨 Schema Parsing Error: Missing 'nodes' or 'relationships' key.", state="error", expanded=True);
                     with tab_schema: schema_placeholder_direct.error("The uploaded JSON is missing the required 'nodes' or 'relationships' top-level keys."); st.json(schema_analysis_dict)
                     st.stop()
                missing_id_prop = [lbl for lbl, data in schema_analysis_dict.get("nodes", {}).items() if not isinstance(data, dict) or not data.get("id_property")]
                if missing_id_prop:
                     status.update(label=f"🚨 Schema Parsing Error: Missing 'id_property' for nodes: {', '.join(missing_id_prop)}.", state="error", expanded=True)
                     with tab_schema: schema_placeholder_direct.error(f"The uploaded schema analysis MUST contain a valid 'id_property' for all nodes when skipping the analysis step. Missing for: {', '.join(missing_id_prop)}"); st.json(schema_analysis_dict)
                     st.stop()
                status.write("✅ Schema Analysis Parsed Directly.")
                with tab_schema: schema_placeholder_direct.empty(); st.info("Using uploaded file directly as schema analysis."); st.json(schema_analysis_dict)

            # --- Save the final schema_analysis_dict (from either path) ---
            try:
                schema_output_file_path = os.path.join(agent_output_path, SCHEMA_ANALYSIS_FILENAME)
                with open(schema_output_file_path, "w", encoding="utf-8") as f:
                    json.dump(schema_analysis_dict, f, indent=2)
                logger.info(f"Schema analysis result saved to {schema_output_file_path}")
            except Exception as e: logger.warning(f"Could not save final schema analysis output: {e}")
            
            # Save the collection of raw batch outputs (if any) for debugging
            if st.session_state.raw_schema_agent_batch_outputs:
                try:
                    with open(os.path.join(agent_output_path, RAW_SCHEMA_AGENT_OUTPUT_FILENAME), "w", encoding="utf-8") as f: json.dump(st.session_state.raw_schema_agent_batch_outputs, f, indent=2)
                except Exception as e: logger.warning(f"Could not save concatenated raw schema batch outputs: {e}")
            
            # --- Step 2: Data Plan Handling (Generate or Upload) ---
            if st.session_state.data_plan_source == 'Upload':
                status.write(f"📈 Loading uploaded Generation Plan file ({uploaded_generation_plan_file.name})...")
                try:
                    uploaded_generation_plan_file.seek(0)
                    plan_content_string = uploaded_generation_plan_file.read().decode("utf-8")
                    generation_plan_dict = safe_json_loads(plan_content_string)
                    if generation_plan_dict is None or not isinstance(generation_plan_dict, dict):
                        status.update(label="🚨 Generation Plan Upload Error: Invalid JSON structure.", state="error", expanded=True)
                        with tab_plan: st.error("Uploaded generation plan file does not contain valid JSON or is not a dictionary."); st.code(plan_content_string or "No content")
                        st.stop()
                    st.session_state.generation_plan_generated = generation_plan_dict
                    status.write("✅ Generation Plan Loaded from Upload.")
                    with tab_plan:
                        st.info(f"Using uploaded generation plan from '{uploaded_generation_plan_file.name}'.")
                        st.json(generation_plan_dict)
                except Exception as upload_err:
                    status.update(label=f"🚨 Generation Plan Upload Error: {upload_err}", state="error", expanded=True)
                    logger.error(f"Error processing uploaded generation plan file: {upload_err}", exc_info=True)
                    with tab_plan: st.error(f"Error reading or parsing uploaded generation plan file: {upload_err}")
                    st.stop()
            else: # Generate Data Plan
                status.write("📈 Running Data Planner Agent...")
                logger.info("Running DataPlanner Agent...")
                with tab_plan: st.info("Data Planner is running..."); plan_placeholder = st.empty()
                planner_input_dict = { "schema_analysis": schema_analysis_dict, "num_stores": num_stores, "num_customers": num_customers }
                planner_input_json_string = json.dumps(planner_input_dict)
                planner_response = data_planner.run(message=planner_input_json_string, stream=False)
                generation_plan_json_string = planner_response.content
                generation_plan_dict = safe_json_loads(generation_plan_json_string)
                if generation_plan_dict is None:
                    status.update(label="🚨 Data Planner Error: Invalid JSON output.", state="error", expanded=True);
                    with tab_plan: plan_placeholder.error("Data Planner Agent returned invalid JSON."); st.code(generation_plan_json_string or "No content")
                    st.stop()
                st.session_state.generation_plan_generated = generation_plan_dict
                actual_total_nodes = sum(generation_plan_dict.values())
                status.write(f"✅ Data Planning Complete (Total Nodes: {actual_total_nodes}).")
                with tab_plan: plan_placeholder.empty(); st.json(generation_plan_dict)

            # --- Save Generation Plan ---
            try:
                plan_output_file_path = os.path.join(agent_output_path, GENERATION_PLAN_FILENAME)
                with open(plan_output_file_path, "w", encoding="utf-8") as f: json.dump(generation_plan_dict, f, indent=2) # Save the dict
                logger.info(f"Data plan saved to {plan_output_file_path}")
            except Exception as e: logger.warning(f"Could not save data plan output: {e}")

            # --- Step 3: Value List Handling (Generate or Upload) ---
            if st.session_state.value_list_source == 'Upload':
                status.write(f"📄 Loading uploaded Value Lists file ({uploaded_value_list_file.name})...")
                try:
                    uploaded_value_list_file.seek(0)
                    value_list_content_string = uploaded_value_list_file.read().decode("utf-8")
                    value_lists_dict = safe_json_loads(value_list_content_string)
                    if value_lists_dict is None or not isinstance(value_lists_dict, dict):
                        status.update(label="🚨 Value List Upload Error: Invalid JSON structure.", state="error", expanded=True)
                        with tab_values: st.error("Uploaded value list file does not contain valid JSON or is not a dictionary."); st.code(value_list_content_string or "No content")
                        st.stop()

                    # Save the uploaded content to the standard filename
                    value_list_output_file_path = os.path.join(agent_output_path, VALUE_LISTS_FILENAME)
                    with open(value_list_output_file_path, "w", encoding="utf-8") as f: json.dump(value_lists_dict, f, indent=2)
                    logger.info(f"Uploaded value lists saved to {value_list_output_file_path}")

                    st.session_state.value_lists_generated = value_lists_dict
                    st.session_state.value_lists_confirmed = True # Mark as confirmed since it was uploaded
                    status.write("✅ Value Lists Loaded from Upload.")
                    with tab_values:
                        st.info(f"Using uploaded value lists from '{uploaded_value_list_file.name}'.")
                        st.json(value_lists_dict) # Display the loaded lists

                except Exception as upload_err:
                    status.update(label=f"🚨 Value List Upload Error: {upload_err}", state="error", expanded=True)
                    logger.error(f"Error processing uploaded value list file: {upload_err}", exc_info=True)
                    with tab_values: st.error(f"Error reading or parsing uploaded value list file: {upload_err}")
                    st.stop()

            else: # Generate Value Lists
                status.write("📝 Generating Realistic Value Lists (Streaming)...")
                logger.info("Running ValueListGenerator in batches with streaming per batch...")
                with tab_values:
                    st.info("Value List Generator is running in batches (this may take a moment)...")
                    overall_status_placeholder = st.empty() # For "Processing batch X/Y"
                    
                    # Expander for current batch details
                    current_batch_details_expander = st.expander("Current Batch Details", expanded=True)
                    with current_batch_details_expander:
                        batch_status_placeholder = st.empty()
                        batch_code_placeholder = st.empty()

                all_node_labels = list(schema_analysis_dict.get("nodes", {}).keys())
                batch_size = 25
                num_batches = math.ceil(len(all_node_labels) / batch_size)
                
                value_lists_dict = {} # Initialize the master dictionary to store merged results
                full_raw_output_for_saving = "" # To save concatenated raw outputs
                failed_value_list_batches = [] # To track batches that failed

                for i in range(num_batches):
                    batch_start_index = i * batch_size
                    batch_end_index = batch_start_index + batch_size
                    current_batch_labels = all_node_labels[batch_start_index:batch_end_index]

                    if not current_batch_labels:
                        continue

                    overall_status_placeholder.info(f"Processing Value Lists - Batch {i+1}/{num_batches} ({len(current_batch_labels)} labels)...")
                    logger.info(f"Processing ValueListGenerator batch {i+1}/{num_batches} for labels: {current_batch_labels}")
                    
                    batch_status_placeholder.info(f"Batch {i+1}: Preparing schema and initiating stream...")
                    batch_code_placeholder.empty() # Clear previous batch's code/stream output

                    # Create a schema chunk for the current batch
                    batch_schema_nodes = {label: schema_analysis_dict["nodes"][label] for label in current_batch_labels}
                    batch_schema_analysis_chunk = {
                        "nodes": batch_schema_nodes,
                        "relationships": schema_analysis_dict.get("relationships", {}), # Include all relationships for context
                        # Potentially add other top-level schema keys if the agent uses them
                    }

                    value_list_input_dict_batch = { # type: ignore
                        "schema_analysis": batch_schema_analysis_chunk,
                        "additional_instructions": additional_planner_instructions
                    }
                    value_list_input_json_batch = json.dumps(value_list_input_dict_batch)

                    # --- START DEBUG LOGGING FOR BATCH 12 (i=11) ---
                    if i == 11: # Batch 12 is when i is 11 (0-indexed)
                        logger.info(f"DEBUG: Preparing to run ValueListGenerator for BATCH 12 (i={i})")
                        logger.info(f"DEBUG: Labels in BATCH 12: {current_batch_labels}")
                        logger.info(f"DEBUG: Input JSON for BATCH 12 (value_list_input_json_batch):\n{value_list_input_json_batch}")
                    # --- END DEBUG LOGGING ---                    
                    if property_grouping_definitions_dict: value_list_input_dict_batch["property_grouping_definitions"] = property_grouping_definitions_dict # Add if defined
                    
                    batch_json_string_accumulator = "" # To accumulate streamed content for the current batch
                    try:
                        batch_status_placeholder.info(f"Batch {i+1}: Streaming output from AI...")
                        stream_iterator = value_list_generator.run(message=value_list_input_json_batch, stream=True)
                        for chunk_count, chunk in enumerate(stream_iterator):
                            if chunk and hasattr(chunk, 'content') and chunk.content:
                                batch_json_string_accumulator += chunk.content
                                # Display raw stream, attempting to format as JSON-like text
                                batch_code_placeholder.code(batch_json_string_accumulator, language="json") 
                                if chunk_count % 5 == 0: # Update status less frequently
                                     batch_status_placeholder.info(f"Batch {i+1}: Streaming output... (received {chunk_count+1} chunks)")

                        # Final accumulated string for this batch
                        final_batch_json_string = batch_json_string_accumulator 
                        full_raw_output_for_saving += f"\n\n--- Batch {i+1} Raw Output ---\n{final_batch_json_string}"
                        logger.info(f"Batch {i+1} streaming complete. Total length: {len(final_batch_json_string)}")
                        batch_status_placeholder.info(f"Batch {i+1}: Stream complete. Parsing JSON...")

                        batch_parsed_dict = safe_json_loads(final_batch_json_string)

                        if batch_parsed_dict is None:
                            logger.error(f"ValueListGenerator batch {i+1} returned invalid JSON.")
                            batch_status_placeholder.error(f"Batch {i+1}: Invalid JSON received. Skipping this batch.")
                            batch_code_placeholder.code(final_batch_json_string or "No content received for this batch", language="text")
                            continue # Skip to next batch
                        
                        # Merge batch results into the main dictionary
                        value_lists_dict.update(batch_parsed_dict)
                        logger.info(f"Successfully processed and merged ValueListGenerator batch {i+1}.")
                        batch_status_placeholder.success(f"Batch {i+1}: Successfully processed and merged.")
                        batch_code_placeholder.json(batch_parsed_dict) # Display the parsed JSON for this batch

                    except Exception as batch_error:
                        # Update overall status and batch-specific placeholders
                        overall_status_placeholder.error(f"Error during Value List generation (Batch {i+1}). See details below or in logs.")
                        batch_status_placeholder.error(f"Batch {i+1}: Error during processing: {batch_error}")
                        batch_code_placeholder.code(batch_json_string_accumulator or f"No content received before error in batch {i+1} (Labels: {current_batch_labels})", language="text")
                        logger.error(f"Error during ValueListGenerator batch {i+1} (Labels: {current_batch_labels}): {batch_error}", exc_info=True)
                        # Update the main status to show an error occurred, but don't stop.
                        status.update(label=f"🚨 Value List Generator Error (Batch {i+1} - Labels: {', '.join(current_batch_labels[:3])}{'...' if len(current_batch_labels) > 3 else ''}): {batch_error}", state="error", expanded=True)
                        failed_value_list_batches.append(i+1) # Record the failed batch number
                        logger.warning(f"Batch {i+1} for ValueListGenerator failed due to: {batch_error}. Continuing with the next batch.")
                        continue # Continue to the next batch

                # After all batches are processed
                overall_status_placeholder.info("All batches processed. Finalizing...")
                try: # Save concatenated raw output
                    with open(os.path.join(agent_output_path, RAW_VALUE_LIST_STREAMED_FILENAME), "w", encoding="utf-8") as f:
                        f.write(full_raw_output_for_saving.strip())
                except Exception as e:
                    logger.warning(f"Could not save concatenated ValueListGenerator raw output: {e}")

                if failed_value_list_batches:
                    overall_status_placeholder.warning(
                        # Note: We don't have the labels for *all* failed batches easily here without more complex tracking.
                        f"Value List generation completed with errors in {len(failed_value_list_batches)} batch(es): {', '.join(map(str, failed_value_list_batches))}. "
                        f"The generated '{VALUE_LISTS_FILENAME}' will exclude data from these failed batches."
                    )
                    # Update the main status as well to reflect partial success if there were failures
                    status.update(label=f"⚠️ Value List Generator: Completed with errors in {len(failed_value_list_batches)} batch(es).", state="warning", expanded=True)
                elif not value_lists_dict: # No failures, but also no data (e.g., no string properties)
                    overall_status_placeholder.warning("No value lists were generated. This could be due to errors in all batches or no string properties needing value lists.")
                    status.update(label="⚠️ Value List Generator: No values generated.", state="warning", expanded=True)
                else:
                    overall_status_placeholder.success("Value Lists Generated Successfully from all processed batches.")
                    status.write("✅ Value Lists Generated (Batched).")
                    st.session_state.value_lists_generated = value_lists_dict
                    st.session_state.value_lists_edited = json.dumps(value_lists_dict, indent=2)

                # --- Update Status - Waiting for User Review (Only if generated) ---
                status.update(label="⏳ Please review and confirm the generated Value Lists in the 'Value Lists' tab.", state="running")

            # --- Step 3.5: Generation Rule Generation (New) ---
            status.write("🔢 Generating Data Type Ranges/Rules...")
            logger.info("Running GenerationRuleGenerator...")
            with tab_gen_rules: st.info("Streaming Generation Rules output..."); gen_rules_stream_placeholder = st.empty()
            gen_rules_full_response_content = ""
            generation_rules_json_string = None

            try:
                gen_rules_input_dict = {"schema_analysis": schema_analysis_dict, "additional_instructions": additional_planner_instructions}
                gen_rules_input_json = json.dumps(gen_rules_input_dict)
                gen_rules_stream_iterator = generation_rule_generator.run(message=gen_rules_input_json, stream=True)
                for chunk in gen_rules_stream_iterator:
                    if chunk and hasattr(chunk, 'content') and chunk.content:
                        gen_rules_full_response_content += chunk.content
                        gen_rules_stream_placeholder.code(gen_rules_full_response_content, language=None)
                generation_rules_json_string = gen_rules_full_response_content
                logger.info("GenerationRuleGenerator streaming finished.")

                try: # Save raw agent output
                    with open(os.path.join(agent_output_path, "generation_rules_agent_output.json"), "w", encoding="utf-8") as f: f.write(generation_rules_json_string or "{}")
                except Exception as e: logger.warning(f"Could not save GenerationRuleGenerator agent output: {e}")

                generation_rules_dict = safe_json_loads(generation_rules_json_string)
                if generation_rules_dict is None:
                    status.update(label="🚨 Generation Rule Error: Invalid JSON output after streaming.", state="error", expanded=True)
                    gen_rules_stream_placeholder.error("GenerationRuleGenerator failed to return valid JSON after streaming.")
                    # st.code(generation_rules_json_string or "No content received", language=None) # Already shown
                    st.stop()
                else:
                    gen_rules_stream_placeholder.success("Generation Rules Created Successfully (Streaming Complete).")
                    status.write("✅ Generation Rules Created.")
                    st.session_state.generation_rules_generated = generation_rules_dict
                    with tab_gen_rules: st.json(generation_rules_dict) # Display final parsed JSON
            except Exception as stream_error:
                status.update(label=f"🚨 Generation Rule Streaming Error: {stream_error}", state="error", expanded=True)
                logger.error(f"Error during GenerationRuleGenerator streaming: {stream_error}", exc_info=True)
                gen_rules_stream_placeholder.error(f"Error during Generation Rule streaming: {stream_error}\n\nPartial content received:\n")
                st.code(gen_rules_full_response_content or "No content received before error", language=None)
                st.stop()
            # Save the generation_rules.json file (moved inside the success block of parsing)
            if generation_rules_dict:
                try:
                    gen_rules_output_file_path = os.path.join(agent_output_path, GENERATION_RULES_FILENAME)
                    with open(gen_rules_output_file_path, "w", encoding="utf-8") as f: json.dump(generation_rules_dict, f, indent=2)
                    logger.info(f"Generation rules saved to {gen_rules_output_file_path}")
                except Exception as e: logger.warning(f"Could not save generation rules output: {e}")

            # --- Parse and Save Cardinality Rules (if provided) ---
            # (This logic remains the same)
            if cardinality_rules_json:
                try:
                    cardinality_rules_dict = json.loads(cardinality_rules_json)
                    if not isinstance(cardinality_rules_dict, dict):
                        st.warning("Cardinality rules input is not a valid JSON object. Ignoring.")
                        cardinality_rules_dict = {}
                    else:
                        # Save the parsed rules
                        try:
                            cardinality_output_file_path = os.path.join(agent_output_path, CARDINALITY_RULES_FILENAME)
                            with open(cardinality_output_file_path, "w", encoding="utf-8") as f: json.dump(cardinality_rules_dict, f, indent=2)
                            logger.info(f"Cardinality rules saved to {cardinality_output_file_path}")
                            st.session_state.cardinality_rules_saved = True
                        except Exception as e: logger.warning(f"Could not save cardinality rules: {e}")
                except json.JSONDecodeError:
                    st.warning("Invalid JSON format in Cardinality Rules. Ignoring.")
                    cardinality_rules_dict = {}
            else:
                 # Ensure the file doesn't exist or is empty if no rules are provided
                 try:
                     cardinality_output_file_path = os.path.join(agent_output_path, CARDINALITY_RULES_FILENAME)
                     if os.path.exists(cardinality_output_file_path):
                         os.remove(cardinality_output_file_path)
                         logger.info(f"Removed existing cardinality rules file as none were provided.")
                 except Exception as e:
                     logger.warning(f"Could not remove old cardinality rules file: {e}")

            # --- Parse Property Grouping Definitions (if provided) ---
            if st.session_state.property_grouping_definitions_content:
                try:
                    property_grouping_definitions_dict = json.loads(st.session_state.property_grouping_definitions_content)
                    if not isinstance(property_grouping_definitions_dict, dict):
                        st.warning("Property Grouping Definitions input is not a valid JSON object. Ignoring.")
                        property_grouping_definitions_dict = {}
                    else:
                        logger.info(f"Property Grouping Definitions parsed: {property_grouping_definitions_dict}")
                except json.JSONDecodeError:
                    st.warning("Invalid JSON format in uploaded Property Grouping Definitions file. Ignoring.")
                    property_grouping_definitions_dict = {}
            else:
                property_grouping_definitions_dict = {} # Ensure it's an empty dict if no file uploaded

        except Exception as e:
            status.update(label=f"🚨 Workflow Error: {e}", state="error")
            logger.error("Agent workflow failed during initial steps.", exc_info=True)
            st.stop() # Stop workflow here if initial steps fail

# --- User Review and Confirmation Step (Only if Value Lists were Generated) ---
if st.session_state.value_list_source == 'Generate' and st.session_state.value_lists_generated and not st.session_state.value_lists_confirmed:
    with tab_values:
        st.info("Review the generated sample values for String properties below. You can edit the JSON directly in the text area.")
        edited_values_json = st.text_area(
            "Editable Value Lists (JSON):",
            value=st.session_state.value_lists_edited,
            height=400,
            key="value_list_editor"
        )
        st.session_state.value_lists_edited = edited_values_json

        confirm_button = st.button("✅ Confirm Edited Values and Continue", type="primary")
        if confirm_button:
            try:
                parsed_edited_values = json.loads(st.session_state.value_lists_edited)
                st.session_state.value_lists_generated = parsed_edited_values
                st.session_state.value_lists_confirmed = True

                # --- Save Confirmed Value Lists ---
                agent_output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), AGENT_OUTPUT_DIR)
                try:
                    value_list_output_file_path = os.path.join(agent_output_path, VALUE_LISTS_FILENAME)
                    with open(value_list_output_file_path, "w", encoding="utf-8") as f: json.dump(st.session_state.value_lists_generated, f, indent=2)
                    logger.info(f"Confirmed value lists saved to {value_list_output_file_path}")
                    st.success("Value lists confirmed and saved!")
                    st.rerun() # Rerun to proceed
                except Exception as e:
                    st.error(f"Failed to save confirmed value lists: {e}")
                    st.session_state.value_lists_confirmed = False # Revert confirmation on save failure

            except json.JSONDecodeError as json_err:
                st.error(f"Invalid JSON in edited value lists: {json_err}. Please correct the JSON structure.")
            except Exception as confirm_err:
                 st.error(f"Error processing edited values: {confirm_err}")

# --- Continue Workflow After Confirmation (or Upload) ---
if st.session_state.value_lists_confirmed and not st.session_state.final_script_generated:
    agent_output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), AGENT_OUTPUT_DIR)
    # Check if essential files exist before proceeding
    required_files = [SCHEMA_ANALYSIS_FILENAME, GENERATION_PLAN_FILENAME, VALUE_LISTS_FILENAME, GENERATION_RULES_FILENAME] # Added new rules file
    missing_files = [f for f in required_files if not os.path.exists(os.path.join(agent_output_path, f))]

    # Ensure generation_rules_generated is also loaded if continuing
    if not st.session_state.generation_rules_generated and os.path.exists(os.path.join(agent_output_path, GENERATION_RULES_FILENAME)):        st.session_state.generation_rules_generated = safe_json_loads(open(os.path.join(agent_output_path, GENERATION_RULES_FILENAME)).read())
    if missing_files:
        st.error(f"Error: Missing required intermediate file(s) to continue: {', '.join(missing_files)}. Please restart the workflow.")
        st.stop()

    with st.status("Continuing Agent Workflow...", expanded=True) as status:
        try:
            # --- Re-Initialize Model if necessary ---
            status.write("🔧 Re-initializing AI Model (if needed)...")
            google_api_key = os.getenv("GOOGLE_API_KEY") or st.secrets.get("GOOGLE_API_KEY")
            if not google_api_key: status.update(label="🚨 Error: Google API key not found.", state="error"); st.stop()
            model_id_for_agno = selected_model_name if selected_model_name.startswith("models/") else f"models/{selected_model_name}"
            agno_gemini_model_wrapper = Gemini(id=model_id_for_agno, api_key=google_api_key)
            python_code_planner.model = agno_gemini_model_wrapper
            python_code_generator.model = agno_gemini_model_wrapper
            status.write("✅ AI Model Ready.")

            # --- Step 4: Python Code Planning ---
            status.write("📋 Running Python Code Planner...")
            code_planner_input_dict = {
                "schema_analysis_filename": SCHEMA_ANALYSIS_FILENAME,
                "generation_plan_filename": GENERATION_PLAN_FILENAME,
                "value_lists_filename": VALUE_LISTS_FILENAME,
                "cardinality_rules_filename": CARDINALITY_RULES_FILENAME,
                "generation_rules_filename": GENERATION_RULES_FILENAME, # Pass new filename
                "enforce_date_consistency": enforce_date_consistency,
                "additional_instructions": additional_planner_instructions
            }
            current_planner_instructions = PYTHON_CODE_PLANNER_BASE_INSTRUCTIONS.replace("output.cypher", output_cypher_filename)
            python_code_planner.instructions = current_planner_instructions
            code_planner_input_json_string = json.dumps(code_planner_input_dict)
            code_planner_response = python_code_planner.run(message=code_planner_input_json_string, stream=False)
            python_code_plan_md = code_planner_response.content
            try: # Save plan output
                with open(os.path.join(agent_output_path, PYTHON_CODE_PLAN_FILENAME), "w", encoding="utf-8") as f: f.write(python_code_plan_md or "")
            except Exception as e: logger.warning(f"Could not save PythonCodePlanner output: {e}")
            if not python_code_plan_md:
                status.update(label="🚨 Code Planner Error.", state="error");
                with tab_code_plan: st.error("No plan generated.")
                st.stop()
            status.write("✅ Python Code Plan Generated.")
            with tab_code_plan: st.markdown(python_code_plan_md)

            # --- Step 5: Python Code Generation (Streaming) ---
            status.write("🐍 Running Python Code Generator (Streaming)...")
            logger.info("Running PythonCodeGenerator (Streaming)...")
            with tab_final_code: st.info("Streaming Python Code Generator output..."); code_stream_placeholder = st.empty()
            code_full_response_content = ""
            generated_code_markdown = None
            final_python_script = None

            try:
                current_generator_instructions = python_code_generator.instructions.replace("generated_data.cypher", output_cypher_filename)
                python_code_generator.instructions = current_generator_instructions
                code_gen_input_message = f"""
                Input Filenames:
                ```json
                {json.dumps({
                    "schema_analysis_filename": SCHEMA_ANALYSIS_FILENAME,
                    "generation_plan_filename": GENERATION_PLAN_FILENAME,
                    "value_lists_filename": VALUE_LISTS_FILENAME,
                    "cardinality_rules_filename": CARDINALITY_RULES_FILENAME,
                    "generation_rules_filename": GENERATION_RULES_FILENAME # Pass to generator
                })}
                ```
                Date Consistency Flag:
                ```json
                {json.dumps(enforce_date_consistency)}
                ```
                Code Plan:
                ```markdown
                {python_code_plan_md}
                ```
                """
                # Call with stream=True
                code_stream_iterator = python_code_generator.run(message=code_gen_input_message, stream=True)

                for chunk in code_stream_iterator:
                    if chunk and hasattr(chunk, 'content') and chunk.content:
                        code_full_response_content += chunk.content
                        # Update placeholder with accumulated content (assume markdown/code)
                        code_stream_placeholder.code(code_full_response_content, language='python') # Display raw stream as python

                # --- After the loop ---
                generated_code_markdown = code_full_response_content # The final accumulated content
                logger.info("PythonCodeGenerator streaming finished.")

                # Save the raw streamed output
                try:
                    with open(os.path.join(agent_output_path, RAW_CODE_GEN_STREAMED_FILENAME), "w", encoding="utf-8") as f: f.write(generated_code_markdown or "")
                except Exception as e: logger.warning(f"Could not save raw streamed code generator output: {e}")

                # Extract Python code from the final accumulated markdown
                final_python_script = extract_python_code(generated_code_markdown)

                if final_python_script is None:
                    status.update(label="🚨 Code Generator Error: Invalid Python code output after streaming.", state="error", expanded=True);
                    code_stream_placeholder.error("Code Generator failed to return valid Python code after streaming.")
                    # Optionally display the raw markdown again below the error
                    st.code(generated_code_markdown or "No content received", language=None)
                    try: # Save failed output (redundant but safe)
                        with open(os.path.join(agent_output_path, FAILED_CODE_OUTPUT_FILENAME), "w", encoding="utf-8") as f: f.write(generated_code_markdown or "")
                    except Exception as e: logger.error(f"Error saving failed PythonCodeGenerator output: {e}")
                    st.stop()
                else:
                    # Success: Update placeholder and session state
                    code_stream_placeholder.success("Python Code Generated Successfully (Streaming Complete).")
                    # Display the *extracted* code cleanly
                    st.code(final_python_script, language="python")
                    status.write("✅ Python Code Generation Complete.")
                    st.session_state.final_script_generated = final_python_script

            except Exception as stream_error:
                status.update(label=f"🚨 Code Generator Streaming Error: {stream_error}", state="error", expanded=True)
                logger.error(f"Error during PythonCodeGenerator streaming: {stream_error}", exc_info=True)
                code_stream_placeholder.error(f"Error during Code Generation streaming: {stream_error}\n\nPartial content received:\n")
                st.code(code_full_response_content or "No content received before error", language=None)
                st.stop()


            # --- Final Success ---
            status.update(label="🎉 Workflow Completed Successfully!", state="complete", expanded=False)

            # --- Save Final Python Script Locally ---
            if final_python_script: # Ensure script exists before saving
                try:
                    agent_output_py_path = os.path.join(agent_output_path, output_py_filename)
                    with open(agent_output_py_path, "w", encoding="utf-8") as f: f.write(final_python_script)
                    st.success(f"✅ Python script saved to: {agent_output_py_path}")
                    st.info(f"Run the script (e.g., `python {agent_output_py_path}`) to generate the '{output_cypher_filename}' file.")
                    st.info(f"Ensure '{SCHEMA_ANALYSIS_FILENAME}', '{GENERATION_PLAN_FILENAME}', '{VALUE_LISTS_FILENAME}', and optionally '{CARDINALITY_RULES_FILENAME}' are in the '{AGENT_OUTPUT_DIR}' directory when running the script.")
                except Exception as e:
                    st.error(f"❌ Error saving Python script locally: {e}")
            else:
                 st.error("❌ Could not save final Python script because generation failed or code extraction failed.")


        except Exception as e:
            status.update(label=f"🚨 Workflow Error: {e}", state="error")
            logger.error("Agent workflow failed during final steps.", exc_info=True)

# --- Display Final Script if already generated ---
elif st.session_state.final_script_generated:
     st.info("Workflow previously completed. Displaying final script.")
     agent_output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), AGENT_OUTPUT_DIR)
     try:
         # Display previous results if files exist
         schema_analysis_display_dict = safe_json_loads(open(os.path.join(agent_output_path, SCHEMA_ANALYSIS_FILENAME)).read())
         with tab_schema: st.json(schema_analysis_display_dict)
         # Display generation plan from session state if available, else from file
         with tab_plan: st.json(st.session_state.generation_plan_generated or safe_json_loads(open(os.path.join(agent_output_path, GENERATION_PLAN_FILENAME)).read()))
         with tab_plan: st.json(safe_json_loads(open(os.path.join(agent_output_path, GENERATION_PLAN_FILENAME)).read()))
         with tab_values: st.json(safe_json_loads(open(os.path.join(agent_output_path, VALUE_LISTS_FILENAME)).read())) # Load saved lists
         with tab_code_plan: st.markdown(open(os.path.join(agent_output_path, PYTHON_CODE_PLAN_FILENAME)).read())
         with tab_final_code: st.code(st.session_state.final_script_generated, language="python")
     except Exception as display_err:
         logger.warning(f"Could not display all previous results: {display_err}")
         with st.expander("🐍 Generated Script", expanded=True): st.code(st.session_state.final_script_generated, language="python")

     st.success("✅ Python script is ready.")
     agent_output_py_path = os.path.join(agent_output_path, output_py_filename)
     st.info(f"Script saved at: {agent_output_py_path}")
     st.info(f"Run the script (e.g., `python {agent_output_py_path}`) to generate the '{output_cypher_filename}' file.")
     st.info(f"Ensure '{SCHEMA_ANALYSIS_FILENAME}', '{GENERATION_PLAN_FILENAME}', '{VALUE_LISTS_FILENAME}', and optionally '{CARDINALITY_RULES_FILENAME}' are in the '{AGENT_OUTPUT_DIR}' directory when running the script.")


# --- Final Message if not generating ---
elif generate_button and (uploaded_schema_file is None or \
                          (st.session_state.data_plan_source == 'Upload' and uploaded_generation_plan_file is None) or \
                          (st.session_state.value_list_source == 'Upload' and uploaded_value_list_file is None)):
    # Warning already shown in sidebar, no need for extra message here
    pass