# Alibaba API Overview

当前的文档文件夹内存放着**阿里巴巴国际站商户询盘**相关的一些 API 的描述信息。

## 关于 API

具体的 API 的端点、域名、请求方法、载荷示例和响应示例，请参见子文件夹内的具体的文档。

## 关于用户 ID

### 三种用户 ID

用户 ID 是一个重要的概念，API 的中可能会出现下面 3 种 ID：

- 阿里 ID（Ali ID）：十几位的数字。这个 ID 用户是看不到的，但是是账号的重要标识；

- 登录 ID（Login ID）：数字和小写字母混合，通常是由两位代表国家的字母+几位数字+几位随机字母组成。这个 ID 是用户拿来登录时输入的。

- 加密 ID：数字和大小写字母混合，通常比较长。较前两种 ID 见得少一点。

> 另外，数据库中用于表示用户的 ID，则是由 Ali ID + 字符串 `@icbu` 拼接而来。

### 我的用户 ID

有两种方法知道我的 ID 信息。

1. 通过 Cookie 获取（较通用）：要想获取我的（当前登录了应用程序的）用户 ID，可以通过请求中的 Cookie 来解析。对 Cookie 使用正则表达式 `xman_i=aid=(\d+)` 即可提取到我的 *Ali ID* 了。

2. 通过监听指定的 API 响应获取（时机性较强但较完整）：监听 `POST /gw/mtop.alibaba.icbu.contact.extinfo.get` 请求（API 定义参见对应文档）。这个请求不仅可以知道 Ali ID，还可以知道其他格式的 ID。但需要注意，这个 API 可能获取的不只是我自己的 ID，可能获取的是其他人的。如果需要用这个 API 获取自身信息，需要结合上面的第 1 种办法（Cookie）来判断响应是不是针对自己的。

## 关于 API 操作能力

### API 请求拦截

目前通过 Yak/Yakit 执行本地 MITM，Python Agent 只启动 HTTP receiver 接收 Yak 脚本转发的流量事件。

实际运行链路是：APP 代理到 `127.0.0.1:8084` Yak/Yakit MITM -> Yak 脚本把流量事件 POST 到 `127.0.0.1:8085` Python Agent receiver -> Python 根据 URL 和响应体解析 API 并写入池子。

当前 Yak 脚本对已知 API 关键词（`queryCustomerInfo`、`getuserinfobyparams`、`icbu.im.id.get`、`contact.extinfo.get`、`fetchcard`）复制响应体并转发到 Python receiver，其余流量只透传。每条命中请求都记录 `count`（含 0）与 `body_bytes`，便于判断导航到底触发了哪个 API；Yak 每 5 分钟打印一行 `[*] Yak MITM tick seen=<全部响应数> matched=<关键词命中数>` 心跳（由 Python 侧补本机时间戳），用于区分「客户端流量没走代理」与「走了但没有匹配 URL」，该行以 `yak.traffic_tick` 事件进入诊断导出。

### API 请求发送

目前不能做到 API 请求的*主动*发送。对 API 的操作能力仅局限于被动监听应用程序的行为。

## 关于聊天消息中的产品卡片

卡片消息（`user_content_type = 10010`）的 `content` BLOB 中内嵌 JSON，`cardType` 标识类型，`params` 携带该类型的字段：

- JSON 键顺序不固定（`params` 可能出现在 `cardType` 之前），前后可能有非 JSON 字节，需以容错方式提取；
- 产品类（`3` / `2000` / `2028` / `2098`）可从 `params.id(s)` / `quoteProductId` 取到商品 ID，与 fetchcard 返回的 `productIdTitle` 数字部分一致；
- 各类卡片的消息内可用字段与展示方式见 [card_types.md](card_types.md)；解析与展示视图统一在 `backend/app/shared/cards.py`；
- fetchcard 池只影响产品卡的标题/价格/图片等富化，不影响卡片本身能否识别。
