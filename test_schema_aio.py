import asyncio
import aiohttp
import json
from aave_config import AAVE_SUBGRAPH_URL

async def fetch_schema():
    query = """
    {
      __type(name: "Position") {
        name
        fields {
          name
          type {
            name
            kind
            ofType {
              name
              kind
            }
          }
        }
      }
      AccountType: __type(name: "Account") {
        name
        fields {
          name
          type {
            name
            kind
            ofType {
              name
              kind
            }
          }
        }
      }
    }
    """
    
    async with aiohttp.ClientSession() as session:
        async with session.post(AAVE_SUBGRAPH_URL, json={"query": query}) as r:
            data = await r.text()
            with open("schema_fields.log", "w") as f:
                f.write(data)

asyncio.run(fetch_schema())
