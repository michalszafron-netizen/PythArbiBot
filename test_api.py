import urllib.request, json
try:
    url = 'https://aave-api-v2.aave.com/data/liquidity/v3?poolId=0x794a61358D6845594F94dc1DB02A252b5b4814aD'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    response = urllib.request.urlopen(req)
    data = json.loads(response.read())
    with open('test_api_out.txt', 'w') as f:
        f.write(str(list(data.keys())))
except Exception as e:
    with open('test_api_out.txt', 'w') as f:
        f.write(repr(e))
