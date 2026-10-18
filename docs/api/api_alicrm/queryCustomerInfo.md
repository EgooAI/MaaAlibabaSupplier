# Customer API: Query Customer Info

获取某个客户的非常详细的信息。

GET /jsonp/customerPluginQueryServiceI/queryCustomerInfo.json?buyerAccountId=MC1...6gNh8&secTradeId=&buyerLoginId=ph123456xxx&secReqToken=wc4BxxxxvAt%2BRRvrxxxxYd4%3D&clientType=&lang=zh_CN&ctoken=tyxxxxem&_tb_token_=e33xxxx3ad&callback=jsonp_17xxx225_73600 HTTP/1.1

Host: alicrm.alibaba.com

Response:

```js
/**/ typeof jsonp_17xxx25_73600 === 'function' &&
jsonp_17xxx25_73600({
  success: true,
  data: {
    data: {
      customerType: 'WAIT_TO_ADD_CUSTOMER',
      hasCreatedCouponPrivilege: true,
      status: 'enabled',
      accioFlag: null,
      alicrmCustomerInfo: {
        customerId: null,
        repurchaseCustomer: false,
        ownerName: null,
        willLoss: false,
        potentialScore: '-1',
        firstName: null,
        lastName: null,
        email: null,
        mobileNumber: null,
        phoneNumber: null,
        companyName: null,
        companyWebSite: null,
        customerGroup: null,
        importanceLevel: null,
        wangwangid: null,
        secToken: null,
        contractId: null,
        noteLabel: null,
        noteCode: null,
        parentNoteCode: null,
        noteContent: null,
        contentCode: null,
        scoreInContent: null,
        taOrderCount: 0,
        taTotalAmount: 0,
        taLastBuyPast: null,
        rebuy: false,
        orderNo: null,
        annualProcurement: null,
        businessTypes: null,
        marketingGroups: null,
        categorys: null,
      },
      buyerInfo: {
        firstName: 'XXX', // 名字
        lastName: 'XXX', // 姓氏
        potentialScore: null,
        headerFullPath: null,
        headerSmallPath: null,
        country: 'PH', // 国家代码
        levelTag: 'C',
        registerDate: 1774700000, // 注册时间
        companyName: 'XXX XXX', // 公司名称
        companyWebSite: null,
        productViewCount: 42, // 最近浏览产品数
        validInquiryCount: 4, // 有效询盘数
        repliedInquiryCount: 3, // 回复询盘数
        validRfqCount: 0, // 有效RFQ数
        loginDays: 19, // 累计登录天数
        spamInquiryMarkedBySupplierCount: 0, // 垃圾询盘数
        addedToBlacklistCount: 0, // 被拉黑数
        totalOrderCount: -1,
        totalOrderVolume: -1,
        tradeSupplierCount: -1,
        isGoldenBuyer: null,
        goldenBuyerLogo: null,
        encryptAccountId: 'MC1...NUl',
        highQualityLevelTag: 'L1+',
        onboardingBusinessType: null,
        onboardingPurchasePreference: null,
        businessTypes: null,
        salesTypes: null,
        buyerContactInfo: {
          visible: false,
          applyStatus: 'NO_SENT',
          email: null, // 电子邮箱，可能存在
          mobileNumber: null, // 移动电话，可能存在
          phoneNumber: null, // 手机号码，可能存在
        },
        searchWords: ['*'],
        preferredIndustries: [
          'Induction Cookers',
          'Portable Power Stations',
          'Camping Cart',
          'Construction Machinery Parts',
          'Fire Pits',
        ], // 兴趣产业
        lastestRfqList: null,
        latestInquiryProducts: [
          'https: //sc04.alicdn.com/kf/Hf5xxx81U.jpg',
          'https: //sc04.alicdn.com/kf/Heaxxx2do.jpg',
          'https: //sc04.alicdn.com/kf/H8axxxa2o.jpg',
          'https: //sc04.alicdn.com/kf/H12xxx33R.jpg',
        ], // 最近询盘产品的图片
        buyerShopBehaviorInfo: null,
        growthLevelInfo: {
            growthLevel: 'L1+',
            pcUrl: 'https: //img.alicdn.com/imgextra/i2/Oxxx1870-2-tps-172-80.png',
            appUrl: 'https: //gw.alicdn.com/imgextra/i2/Oxxx1870-2-tps-172-80.png',
          },
        },
      extValues: {},
      isAdmin: true,
      isGgsOrIfm: false,
      isCGS: true,
    },
  },
  message: '',
  code: '',
});
```
