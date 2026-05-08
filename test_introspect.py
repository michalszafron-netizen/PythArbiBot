import requests
import json
from dotenv import load_dotenv
import os

load_dotenv()
url = os.getenv("AAVE_SUBGRAPH_URL")

query = {"query": "{ __schema { queryType { fields { name } } } }"}

try:
    r = requests.post(url, json=query)
    with open("schema_introspect.log", "w") as f:
        f.write(f"Status: {r.status_code}\n")
        f.write(r.text)
except Exception as e:
    with open("schema_introspect.log", "w") as f:
        f.write(str(e))
