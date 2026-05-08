import os
import requests
from dotenv import load_dotenv

load_dotenv()
url = os.getenv("AAVE_SUBGRAPH_URL")

query = """
{
  __schema {
    queryType {
      fields {
        name
      }
    }
  }
}
"""

try:
    response = requests.post(url, json={'query': query})
    data = response.json()
    with open("schema_out.txt", "w") as f:
        f.write(str(data))
except Exception as e:
    with open("schema_out.txt", "w") as f:
        f.write(str(e))
