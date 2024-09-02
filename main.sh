sahab_lat=35.736000000000004
sahab_long=51.440000000000055

tarasht_lat=35.71062963544567
tarasht_long=51.34362036878247

ORIGIN_LAT=$sahab_lat
ORIGIN_LONG=$sahab_long

DEST_LAT=$tarasht_lat
DEST_LONG=$tarasht_long

source ./default.env

curl 'https://api.tapsi.cab/api/v3/ride/preview' \
  -s \
  --compressed \
  -X POST \
  -H 'User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0' \
  -H 'Accept: */*' \
  -H 'Accept-Language: en-US,en;q=0.5' \
  -H 'Accept-Encoding: gzip, deflate, br, zstd' \
  -H 'x-agent: v2.2|passenger|WEBAPP|7.5.0||5.0' \
  -H 'Origin: https://app.tapsi.cab' \
  -H 'Sec-Fetch-Dest: empty' \
  -H 'Sec-Fetch-Mode: cors' \
  -H 'Sec-Fetch-Site: same-site' \
  -H 'Connection: keep-alive' \
  -H "Cookie: accessToken=$TAPSI_ACCESS_TOKEN; refreshToken=$TAPSI_REFRESH_TOKEN" \
  -H 'Connection: keep-alive' \
  --data-raw "{'origin':{'latitude': $ORIGIN_LAT, 'longitude': $ORIGIN_LONG},
    'destinations': [ {'latitude': $DEST_LAT,'longitude': $DEST_LONG} ],
    'hasReturn':false,
    'waitingTime':0,
    'gateway':'CAB',
    'initiatedVia':'WEB',
    'metadata':{'flowType':'DESTINATION_FIRST',
    'previewType':'ORIGIN_FIRST'}}" |
     jq .data.categories[0].items[0].service.prices[0] |
     jq -r '"\(.passengerShare) - \(.discount)"'

curl -s 'https://app.snapp.taxi/api/api-base/v2/passenger/newprice/s/6/0' \
  --compressed \
  -X POST \
  -H 'Content-Type: application/json' \
  -H 'Referer: https://app.snapp.taxi/pre-ride?rideFrom={%22options%22:{%22serviceType%22:1,%22recommender%22:%22cab%22}}' \
  -H 'Origin: https://app.snapp.taxi' \
  -H 'Sec-Fetch-Dest: empty' \
  -H "Authorization: Bearer $SNAPP_ACCESS_TOKEN" \
  -H 'Connection: keep-alive' \
  --data-raw '{ "points":[{"lat":'$ORIGIN_LAT',"lng":'$ORIGIN_LONG'}, {"lat":'$DEST_LAT',"lng":'$DEST_LONG'}, null],
  "voucher_code": null,
  "service_types": [1,2],
  "priceriderecom": false,
  "tag": "0",
  "serviceType": 1,
  "hurryRaised": 0 }' |
  jq .data.prices[0].final
