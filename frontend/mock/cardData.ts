import { documentCardsToBusinessCards } from "@/domain/cards/cardModel";
import type { GenericCard, InquiryCard, ProductCard } from "@/types/cards";

export const productCards: ProductCard[] = [
  {
    card_id: "card-product-001",
    title: "智能仓储温控传感器套装",
    price: "USD 39.00",
    display_price: "USD 32.00-39.00",
    product_image: "/mock/product-temperature-sensor.png",
    moq: "100",
    moq_unit: "套",
    product_id: "prd-88001",
    product_url: "https://example.alibaba.com/product/prd-88001",
    expired: false,
  },
  {
    card_id: "card-product-002",
    title: "太阳能庭院灯 OEM 套装",
    price: "USD 12.50",
    display_price: "USD 9.90-12.50",
    product_image: "/mock/product-solar-light.png",
    moq: "300",
    moq_unit: "件",
    product_id: "prd-88002",
    product_url: "https://example.alibaba.com/product/prd-88002",
    expired: false,
  },
  {
    card_id: "card-product-003",
    title: "工业泵 OEM 方案卡",
    price: "USD 86.00",
    display_price: "USD 72.00-86.00",
    product_image: "/mock/product-industrial-pump.png",
    moq: "50",
    moq_unit: "台",
    product_id: "prd-88003",
    product_url: "https://example.alibaba.com/product/prd-88003",
    expired: true,
  },
];

export const inquiryCards: InquiryCard[] = [
  {
    inquiry_id: "inq-20260907-001",
    inquiry_content: "客户询问太阳能庭院灯样品费用、CE 证书、包装和交期。",
    products: [
      {
        product_name: "太阳能庭院灯 OEM 套装",
        product_id: "prd-88002",
        product_unit_price: "USD 12.50",
        product_moq: "300",
        product_unit: "件",
        product_image: "/mock/product-solar-light.png",
        discount_price: "USD 9.90",
        product_url: "https://example.alibaba.com/product/prd-88002",
      },
    ],
    product_image: "/mock/product-solar-light.png",
    is_seller: true,
    attachment_count: "2",
  },
  {
    inquiry_id: "inq-20260907-002",
    inquiry_content: "客户关注冷链传感器 500 套阶梯价与 24 个月质保。",
    products: [
      {
        product_name: "智能仓储温控传感器套装",
        product_id: "prd-88001",
        product_unit_price: "USD 39.00",
        product_moq: "100",
        product_unit: "套",
        product_image: "/mock/product-temperature-sensor.png",
        discount_price: "USD 32.00",
        product_url: "https://example.alibaba.com/product/prd-88001",
      },
    ],
    product_image: "/mock/product-temperature-sensor.png",
    is_seller: true,
    attachment_count: "1",
  },
];

export const genericCards: GenericCard[] = [
  {
    card_type: 101,
    card_id: "card-generic-001",
    source_url: "https://example.alibaba.com/cards/fair-follow-up",
    raw_json: JSON.stringify({
      title: "展会后客户跟进话术",
      summary: "适合展会名片导入后的首次触达。",
      owner: "增长运营 Mina",
      scenario: "展会线索进入 CRM 后自动建议首次跟进。",
      tags: ["跟进", "展会", "高意向"],
    }),
  },
  {
    card_type: 102,
    card_id: "card-generic-002",
    source_url: "https://example.alibaba.com/cards/restock-reminder",
    raw_json: JSON.stringify({
      title: "节日大促补货提醒",
      summary: "结合历史采购记录提醒客户提前锁单和备货。",
      owner: "客户成功 Nora",
      scenario: "老客进入补货周期或旺季前 45 天触达。",
      tags: ["复购", "补货", "大促"],
    }),
  },
];

export const businessCards = documentCardsToBusinessCards({ productCards, inquiryCards, genericCards });
