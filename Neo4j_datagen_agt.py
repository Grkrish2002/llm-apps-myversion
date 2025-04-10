import os
import streamlit as st
from textwrap import dedent
from agno.agent import Agent
from agno.models.google import Gemini
import google.generativeai as genai
import json
import markdown

# --------- LOAD API KEY ---------
google_api_key = os.getenv("GOOGLE_API_KEY")
if not google_api_key:
    st.error("Google API key not found. Please set the GOOGLE_API_KEY environment variable.")
    st.stop()

# Configure the Google API key for genai
genai.configure(api_key=google_api_key)

# Function to get available Gemini models
def get_available_gemini_models():
    """Retrieves and returns a list of available Gemini models."""
    try:
        model_list = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        return model_list
    except Exception as e:
        st.error(f"Error retrieving Gemini models: {e}")
        return []

# Get the list of available models
available_models = get_available_gemini_models()

# Default model
default_model_id = "gemini-2.5-pro-exp-03-25"

# --------------- TITLE AND INFO SECTION -------------------
st.title("Neo4j Data Generation Agent 🧠")
st.write("Analyze your Neo4j schema and generate realistic fake data!")

# --------------- SIDEBAR CONTROLS -------------------
with st.sidebar:
    st.markdown("<h3 style='color: black;'>Select Gemini Model</h3>", unsafe_allow_html=True)
    if available_models:
        st.markdown("<p style='color: black;'>Available Models:</p>", unsafe_allow_html=True)
        selected_model_id = st.selectbox(
            "",
            available_models,
            index=available_models.index(f'models/{default_model_id}') if f'models/{default_model_id}' in available_models else 0
        )
        selected_model_id = selected_model_id.replace('models/', '')
    else:
        st.warning("No Gemini models found.")
        selected_model_id = default_model_id

# --------------- AGENT INITIALIZATION -------------------
# Analyze Agent
analyze_agent = Agent(
    model=Gemini(id=selected_model_id, api_key=google_api_key),
    instructions=dedent("""\
        You are an expert in Neo4j graph databases and Retail ontology.
        You will be provided with a JSON schema representing a Retail Neo4j graph.
        Your task is to:
        1. Analyze the schema and identify the properties of nodes and relationships.
        2. Create a detailed plan for generating realistic fake data for these properties.
        3. Ensure the plan respects data types and integrity constraints.
        Return the plan in a clear and concise manner.
        """),
    markdown=True,
)

# Data Generation Agent
data_gen_agent = Agent(
    model=Gemini(id=selected_model_id, api_key=google_api_key),
    instructions=dedent("""\
        You are an expert in Python programming and Neo4j graph databases.
        Based on the provided data generation plan, write a Python program that:
        1. Generates realistic fake data for the Retail domain based on the Neo4j schema.
        2. Ensures the generated data matches the data types of properties defined in the schema.
        3. Python program writes the Cypher queries to a .cypher file.
        4. The generated data should be realistic and follow the patterns of the Retail domain.
        5. The program should be modular and easy to understand.
        6. The generated data should be unique and not violate any constraints.
        7. Does not violate any integrity constraints.
        8. The program should be able to generate data for different types of nodes and relationships in the schema.
        9. The program should be able to generate data for different types of properties (e.g., strings, integers, dates, etc.).
        10. The program should be efficient and optimized for performance.
        11. The program should be able to handle large datasets.
                             
        Return the Python program as output.
        """),
    markdown=True,
)

# --------------- JSON FILE UPLOAD -------------------
uploaded_json_file = st.file_uploader("Upload your Neo4j schema JSON file", type=["json"])
json_file_content = ""
if uploaded_json_file is not None:
    try:
        json_file_content = json.load(uploaded_json_file)
        st.success("JSON file uploaded successfully!")
    except Exception as e:
        st.error(f"Error reading JSON file: {e}")

# --------------- ANALYZE AGENT -------------------
if json_file_content:
    st.subheader("Analyze Neo4j Schema")
    
    # Add a text input field for additional details
    additional_details = st.text_area(
        "Provide additional instructions for the data generation analysis (optional):",
        placeholder="E.g., Specify constraints, data generation rules, or domain-specific requirements."
    )
    
    # Add a button to start the data generation process
    if st.button("Start Analysis"):
        with st.spinner("Analyzing the schema..."):
            # Combine the JSON schema and additional details into the prompt
            analyze_prompt = f"Neo4j Schema:\n{json.dumps(json_file_content, indent=2)}\n\nAdditional Details:\n{additional_details}"
            
            # Run the Analyze Agent
            analyze_response = analyze_agent.run(analyze_prompt, stream=False)
            analyze_output = analyze_response.content
            st.markdown(f"**Analysis Plan:**\n{analyze_output}")

            # Save the analysis plan to an HTML file
            st.download_button(
                label="Download Analysis Plan",
                data=f"<html><body><pre>{analyze_output}</pre></body></html>",
                file_name="analysis_plan.html",
                mime="text/html",
            )

        # --------------- DATA GENERATION AGENT -------------------
        st.subheader("Generate Data")
        with st.spinner("Writing the data generation program..."):
            data_gen_prompt = f"Data Generation Plan:\n{analyze_output}"
            data_gen_response = data_gen_agent.run(data_gen_prompt, stream=False)
            data_gen_output = data_gen_response.content
            st.markdown(f"**Generated Python Program:**\n```python\n{data_gen_output}\n```")

            # Write the generated Python program to a file in the current directory
            current_dir = os.path.dirname(os.path.abspath(__file__))
            output_file_path = os.path.join(current_dir, "data_generation_new.py")
            try:
                with open(output_file_path, "w", encoding="utf-8") as f:
                    f.write(data_gen_output)
                st.success(f"Python program written to {output_file_path}")
            except Exception as e:
                st.error(f"Error writing Python program to file: {e}")
