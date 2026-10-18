# ICBU IM API: Get user info by params

通过 Ali ID 和 Chat Token 来*批量*（通常包含十几个）获取给定用户的基础信息。

POST /gw/mtop.alibaba.icbu.im.getuserinfobyparams/1.1/?&type=originaljson

Host: acs.m.alibaba.com

Request payload `data=<url encoded JSON>`, where the JSON is like:

```jsonc
{
    "chatTokens": "tokenStringForUser1,tokenStringForUser2,...", // 逗号分隔的字符串，每个 token 对应一个用户
    "queryParams": "[\"buyerNewFlag\", \"countryCode\"]", // 固定
    "contactAliIds": "[\"12345678\",\"23456789\",...]", // 每个 ID 对应一个用户，顺序和 chatTokens 中的一致
    "queryType": "get_buyer_member_info" // 固定
}
```

Response:

```jsonc
{
    "api": "mtop.alibaba.icbu.im.getuserinfobyparams",
    "data": {
        "object": [
            {
                "aliId": 12345678, // Ali ID
                "available": true,
                "countryCode": "ES",
                "countryIcon": "https://u.alicdn.com/mobile/g/common/flags/1.0.0/assets/es.png",
                "emailValidation": false,
                "joiningYears": 0,
                "loginId": "es123456xxx", // 登录名，重要
                "potentialScore": 0,
                "recentContact": false
            },
            {
                "aliId": 23456789,
                "available": true,
                "countryCode": "CN",
                "countryIcon": "https://u.alicdn.com/mobile/g/common/flags/1.0.0/assets/cn.png",
                "emailValidation": false,
                "joiningYears": 0,
                "loginId": "cn123456xxx",
                "potentialScore": 0,
                "recentContact": false
            }
        ]
    },
    "ret": [
        "SUCCESS::调用成功"
    ],
    "v": "1.1"
}
```
