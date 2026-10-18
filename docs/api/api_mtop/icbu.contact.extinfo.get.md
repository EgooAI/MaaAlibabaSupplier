# ICBU API: Get Contact Extended Information

通过 Ali ID 获得指定的用户的基础信息（Contact 接口途径）。

POST /gw/mtop.alibaba.icbu.contact.extinfo.get/1.0/?&type=originaljson

Host: acs.m.alibaba.com

Request payload `data=<url encoded JSON>`, where the JSON is like:

```jsonc
{
    "cid": "",
    "aliIds": "12345678",
    "chatTokens": ""
}
```

Response:

```jsonc
{
    "api": "mtop.alibaba.icbu.contact.extinfo.get",
    "data": {
        "code": "0",
        "data": {
            "accountInfoList": [
                {
                    "accountIdEncrypt": "MC1...WkD", // 加密 ID
                    "accountStatus": "enabled",
                    "aliId": 12345678, // Ali ID，和传入的一样
                    "avatarUrl": "",
                    "companyName": "公司全称",1
                    "country": "CN",
                    "firstName": "XXX",
                    "lastName": "XXX",
                    "loginId": "xxx", // 登录名 ID
                    "vaccountId": 12345 // 不关注这个
                }
            ]
        }
    },
    "ret": [
        "SUCCESS::调用成功"
    ],
    "v": "1.0"
}
```
