# INTL API: Fetch card

获取一个聊天卡片的详情信息。

POST /gw/mtop.alibaba.intl.mobile.interaction.fetchcard/1.1/?&type=originaljson

Host: acs.m.alibaba.com

Request payload `data=<url encoded JSON>`, where the JSON is like:

```jsonc
{
    "paramsMap": "{\"toUid\":\"12345678\",\"fromUid\":\"23456789\",\"sign\":\"xxxxxx\",\"ctime\":\"1775907000000\",\"id\":\"1234567890\"}",
    "language": "zh", // 固定
    "fromAliId": "23456789", // 和 paramsMap 里的是一样的
    "appCountry": "CN", // 固定
    "terminalType": "pc_windows", // 固定
    "toAliId": "12345678", // 和 paramsMap 里的是一样的
    "cardTypeValue": "2028"
}
```

Response:

```jsonc
{
    "api": "mtop.alibaba.intl.mobile.interaction.fetchcard",
    "data": {
        "fbCardList": [
            { "data": { ... } }
        ]
    },
    "ret": ["SUCCESS::调用成功"],
    "v": "1.1"
}
```

## 卡片类型分析

### 产品卡片

```jsonc
{ // 这是 fbCardList 内的一个对象
    "data": {
        "productIdTitle": "产品 ID: 1234567890",
        "title": "Heavy Duty Waterproof Folding Camping Wagon ...", // Important，实际标题
        "price": "$12.90-16.80", // Important
        "displayPrice": "$12.90-16.80", // Important
        "moq": "1", // Important
        "moqUnit": "Unit", // Important
        "productImage": "https://sc04.alicdn.com/kf/H0b...c7t.png_350x350.png",
        "hsfImg": "https://sc04.alicdn.com/kf/H0b...c7t.png_350x350.png",
        "hsfId": "123456789",
        "expiredTitle": "产品已过期", // 意思不是已经过期了，而是过期显示这个标题
        "productAction": {
            "actionParams": {
                "url": "https://chinese.alibaba.com/product-detail/Hea...789.html"  // Important
            },
            "actionName": "openUrl"
        },
        "actionList": [ ... ],
        "benefitFirstText": "无忧退货",
        "promotionDesc": "无忧退货",
        "isPromotion": false,
        "discountPrice": "",
        "scene": "ta",
        "exposeInfo": { "name": "product_card_single" },
        "backgroundColor": "#FFFFFF",
        "foregroundColor": "#F4F4F4"
    },
    "summary": {  // Important
        "tag": "产品",
        "content": "Heavy Duty Waterproof Folding Camping Wagon ..."
    },
    "templateType": "dx",
    "template": {
        // ...
    }
}
```

### 询盘卡片

```jsonc
{ // 这是 fbCardList 内的一个对象
    "data": {
        "businessInfoName": "企业信息",
        "detailAction": {
            // ...
        },
        "isRobot": false,
        "pclandingurl": "https://message.alibaba.com/message/maDetail.htm?imInquiryId=204...tQ5qo/",
        "richTextContent": "Hi,I&rsquo;d like to begin discussions about this item.",
        "isImmediatelyQuotation": true,
        "contactTargetAction": {},
        "inquiryAction": {},
        "moreProductsStr": "还有-1个商品",
        "quoteStr": "立即报价",
        "productAttr": [
            // ...
        ],
        "productImage": "https://sc04.alicdn.com/kf/H869...7a81.png",
        "oldPC": false,
        "inquiryContent": "Hi,I&rsquo;d like to begin discussions about this item.", // Important
        "contactTargetText": "",
        "inquiryCardDTO": {
            // ...
        },
        "attachmentStr": "0个附件",
        "showMoreAttr": false,
        "foregroundColor": "#F4F4F4",
        "contentTitle": "详细需求",
        "fileImg": "https://img.alicdn.com/imgextra/i3/O1C...1.png",
        "moreProductNum": -1,
        "inquiryID": "12345678", // Important
        "isReceiver": true,
        "emptyPriceDesc": "联系获得报价",
        "actionName": "查看详情",
        "showBusinessInfo": false,
        "detailStr": "查看详情",
        "moreAttrNum": -1,
        "moreAttrsStr": "还有-1个属性",
        "content": "Hi,I&rsquo;d like to begin discussions about this item.",
        "products": [ // Important
            {
                "detailAction": {
                    "actionParams": {
                        "url": "https://chinese.alibaba.com/product-detail/Lux...2269.html" // Important
                    },
                    "traceInfo": {
                        "clickContentId": 160123456789,
                        "name": "product_click"
                    },
                    "actionName": "openUrl"
                },
                "productImage": "https://sc04.alicdn.com/kf/H86...a81.png", // Important
                "productId": 160123456789, // Important
                "discountPrice": "", // Important
                "productUnitPrice": "US$39-59", // Important
                "productUnit": "piece", // Important
                "productMOQ": "2 ", // Important
                "productName": "Luxury Modern Beige ... Camping Single" // Important
            }
        ],
        "hasAttachment": false,
        "pcaction": "openinworkspace",
        "hsfId": 160123456789, // Not important, usually equals to productId
        "showMoreProduct": false,
        "attachStr": "附件",
        "backgroundColor": "#FFFFFF",
        "isSeller": true,
        "pctype": "6",
        "isApp": false,
        "terminal": "one_page",
        "cardTitle": "询盘",
        "businessInfo": "",
        "productTitle": "Inquiry from Product Details Page",
        "muchProducts": false,
        "quoteAction": {
            "actionParams": {
                "loginID": "us29227967360uwki",
                "id": "100",
                "title": "询盘",
                "uniqueID": 12345678,
                "url": "https://message.alibaba.com/message/quotationForm.htm?toUserId=MC1ID...282269"
            },
            "actionName": "openUrl"
        },
        "hsfImg": "https://sc04.alicdn.com/kf/H86...7a81.png",
        "clickContentId": "12345678",
        "attachmentCount": "0",
        "attributes": [
            // ...
        ]
    },
    "expiryPeriodInMinutes": 0,
    "layout": {
        "heightType": "wrap",
        "widthType": "fill"
    },
    "needRequest": false,
    "summary": {  // Important
        "tag": "询盘",
        "content": "Inquiry from Product Details Page"
    },
    "template": {
        // ...
    },
    "templateType": "dx"
}
```
