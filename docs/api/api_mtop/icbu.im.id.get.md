# ICBU API: Get Id

通过 Ali ID 获取一个用户的会员 ID 和登录名（IM 接口途径）。

POST /gw/mtop.alibaba.icbu.im.id.get/1.0/?&type=originaljson

Host: acs.m.alibaba.com

Request payload `data=<url encoded JSON>`, where the JSON is like:

```jsonc
{
    "chatTokens": "xxxxxx",
    "ids": "12345678",
    "type": "ALI_ID"
}
```

Response:

```jsonc
{
    "api": "mtop.alibaba.icbu.im.id.get",
    "data": {
        "object": [
        {
            "aliId": 123456789, // 传入的 Ali ID
            "aliMemberId": 123456, // 会员 ID
            "extInfo": {},
            "loginId": "cn12345xxx" // 登录名
        }
        ]
    },
    "ret": ["SUCCESS::调用成功"],
    "v": "1.0"
}
```
