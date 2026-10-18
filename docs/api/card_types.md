# 聊天卡片类型

聊天消息中 `user_content_type = 10010` 的消息均为卡片消息，其 `content` BLOB 中内嵌 JSON 包含 `cardType` 字段，标识卡片类型。

## 数据格式

`content` 为二进制 BLOB，JSON 对象前后可能各有少量非 JSON 字节，且键顺序不固定（`params` 可能在 `cardType` 之前）。

解析统一由 `backend/app/shared/cards.py` 负责：以 latin1 解码后逐 `{` 尝试 `json.JSONDecoder.raw_decode`，命中首个含 `cardType` 的对象后再按 UTF-8 重解该片段，避免中文内容乱码。

## 类型与可显示字段

| cardType | 名称 | 消息内可用字段 | 展示 |
|----------|------|----------------|------|
| 3 | 产品卡片 | `params.id` | 商品 ID；fetchcard 池命中时显示标题/价格/MOQ/图片/链接 |
| 2000 | 产品批量 | `params.ids`（逗号分隔） | 商品 ID 列表；同上富化 |
| 2028 | 报价 | `params.id` | 同产品卡片 |
| 2098 | 报价提醒 | `params.quoteProductId`、`quoId` | 商品/报价 ID |
| 6 | 询盘/反馈 | 仅加密字段 | 仅类型提示，不显示密文 |
| 9 | 订单/交易 | `orderId`、`contractId`、`params`(base64 JSON) | 订单号/合同号/金额/币种/状态 |
| 12 | 文件附件 | `name`、`extensionType`、`size`、`downloadUrl` | 文件名标题、类型/大小摘要、下载链接 |
| 2013 | 收货地址 | `contactName`、`companyAddress`、`companyTel` | 地址卡 |
| 2086 | 关注提醒 | `buyerName` | 买家名 |
| 2106 | 关注提醒 | `remindBuyers`（JSON 数组） | 买家数量 |
| 1 / 23 | 资质认证 | 展示开关 | 仅类型提示 |
| 2008 | 商品目录 | `catalogId` | 目录 ID |
| 2111 | 询价 | `productId`、`skuSelectList`、`totalPrice` | 数量与总价 |
| 8 / 20 / 2003 / 2017 | 未知 | `id` / `encryId` / `cardId` | 类型号 + 非敏感 ID |
| 其他 | 未知 | 可能仅有 `cardType` | 「卡片（类型 N）」 |

密文字段（`sign`、`chatToken`、`encry*`、`*Encrypt`）不会进入 API 响应；`data/pools.db` 的 `generic_card` 表与旧询盘/generic 解析器已不再读写（历史库中可能仍留有旧表，可忽略）。

## 产品卡富化

`fetchcard` 拦截到的产品详情写入 `ProductCardPool`（`data/pools.db`，`product_card` 表）。实测客户端只在收到新的卡片消息时请求卡片详情；已缓存的历史卡片在打开会话时不会重新请求，因此线上会长期存在「有卡片消息但池为空」。未命中详情前卡片按商品 ID 展示；若同一商品之后作为新消息再次出现，池命中后引用它的历史卡片也会一并富化。

`backend/app/shared/backend/card_sweep_service.py` 仅在经 `POST /api/cards/sweep` 手动触发时按会话导航去激活请求（无定时轮询），状态见 `/api/status` 的 `workers.card-sweep`。手动触发需携带 `X-Account-Epoch`，与其它账号写请求一致。诊断日志中每次 sweep 记录 `card.sweep_selected`（`targets`）与逐目标 `card.sweep_target`（`sid`、`navigated`、`unresolved`、`resolved`），实际扫描后记录 `card.sweep_done`（`targets`/`visited`/`enriched`/`failed`）；`yak.traffic_tick`（`seen`/`matched`）说明客户端流量是否真正经过 Yak 代理，各 MITM 路由的 `count`/`body_bytes`（含 0）说明导航触发了哪些 API，`/api/status` 的 `pools.product_cards` 给出池内产品卡数量。
