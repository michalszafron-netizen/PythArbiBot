import requests
import json
import sys
import aave_config

url = aave_config.AAVE_SUBGRAPH_URL

# Step 1: Introspect the Position type to discover available fields
introspect_query = """
{
  __type(name: "Position") {
    fields {
      name
      type { name kind ofType { name } }
    }
  }
}
"""

# Step 2: Also introspect Position_filter to see what where-clause fields exist
filter_query = """
{
  __type(name: "Position_filter") {
    inputFields {
      name
      type { name kind ofType { name } }
    }
  }
}
"""

results = {}
for label, q in [("Position_fields", introspect_query), ("Position_filter", filter_query)]:
    try:
        r = requests.post(url, json={"query": q}, timeout=15)
        results[label] = r.json()
    except Exception as e:
        results[label] = str(e)

with open("schema_introspect.json", "w") as f:
    f.write(json.dumps(results, indent=2))
print("Done — wrote schema_introspect.json")
