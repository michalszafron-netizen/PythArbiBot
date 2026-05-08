import requests, json, os, sys, traceback

# Write to a KNOWN location first to prove file I/O works
test_path = r"c:\Users\markowyy\Documents\ArbitrageBot\ClaudeMOnster\PythOracle\data\sg_check.txt"

try:
    url = "https://gateway.thegraph.com/api/273bca6dba3810c4dfbb103666609a43/subgraphs/id/4xyasjQeREe7PxnF6wVdobZvCw5mhoHZq3T7guRpuNPf"
    
    lines = [f"URL: {url}", "Script started OK"]
    
    # 1) Introspect Position type
    q = '{ __type(name: "Position") { fields { name } } }'
    r = requests.post(url, json={"query": q}, timeout=30)
    d = r.json()
    flds = d.get("data",{}).get("__type",{}).get("fields",[])
    if flds:
        lines.append(f"\nPosition fields ({len(flds)}):")
        for f in flds:
            lines.append(f"  {f['name']}")
    else:
        lines.append(f"\nNo Position type found. Response: {json.dumps(d)[:500]}")
    
    # 2) Introspect Position_filter  
    q2 = '{ __type(name: "Position_filter") { inputFields { name } } }'
    r2 = requests.post(url, json={"query": q2}, timeout=30)
    d2 = r2.json()
    flds2 = d2.get("data",{}).get("__type",{}).get("inputFields",[])
    if flds2:
        kws = ["balance","close","side","hash","debt","borrow","amount","active","open"]
        matches = [f["name"] for f in flds2 if any(k in f["name"].lower() for k in kws)]
        lines.append(f"\nPosition_filter matching fields ({len(matches)} of {len(flds2)} total):")
        for m in matches:
            lines.append(f"  {m}")
    else:
        lines.append(f"\nNo Position_filter found. Response: {json.dumps(d2)[:500]}")
    
    # 3) Test: balance_gt 0
    q3 = '{ positions(first:3, where:{side:BORROWER, balance_gt:"0"}, orderBy:balance, orderDirection:desc) { id balance side account{id} } }'
    r3 = requests.post(url, json={"query": q3}, timeout=30)
    d3 = r3.json()
    lines.append(f"\nTest query result:")
    lines.append(json.dumps(d3, indent=2)[:1500])
    
    with open(test_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

except Exception as e:
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(f"FATAL: {traceback.format_exc()}")
