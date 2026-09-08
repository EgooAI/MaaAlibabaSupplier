import type { BusinessCard, GenericCard, InquiryCard, ProductCard } from "@/types/cards";

export function productCardToBusinessCard(card: ProductCard): BusinessCard {
  return {
    id: card.card_id,
    title: card.title,
    type: "product",
    summary: `${card.display_price || card.price} · MOQ ${card.moq}${card.moq_unit}`,
    tags: ["产品卡", card.product_id].filter(Boolean),
    coverTone: "#e6f4ff",
    details: [
      { label: "价格", value: card.display_price || card.price },
      { label: "MOQ", value: `${card.moq}${card.moq_unit}` },
      { label: "商品 ID", value: card.product_id },
      { label: "链接", value: card.product_url },
    ],
  };
}

export function inquiryCardToBusinessCard(card: InquiryCard): BusinessCard {
  const firstProduct = card.products[0];
  return {
    id: card.inquiry_id,
    title: firstProduct?.product_name ?? "询盘卡片",
    type: "inquiry",
    summary: card.inquiry_content,
    tags: ["询盘", `${card.products.length} 个商品`, `${card.attachment_count} 个附件`],
    coverTone: "#fff7e6",
    details: [
      { label: "询盘 ID", value: card.inquiry_id },
      { label: "商品数", value: String(card.products.length) },
      { label: "附件", value: card.attachment_count },
      ...(firstProduct ? [{ label: "首个商品", value: firstProduct.product_name }] : []),
    ],
  };
}

export function genericCardToBusinessCard(card: GenericCard): BusinessCard {
  const payload = parseGenericPayload(card.raw_json);
  return {
    id: card.card_id,
    title: payload.title ?? `通用卡片 ${card.card_id}`,
    type: "generic",
    summary: payload.summary ?? "通用运营卡片",
    owner: payload.owner,
    tags: payload.tags.length ? payload.tags : ["通用卡", `类型 ${card.card_type}`],
    coverTone: "#f6ffed",
    recommendedScenario: payload.scenario,
    details: [
      { label: "卡片类型", value: String(card.card_type) },
      { label: "来源", value: card.source_url },
    ],
  };
}

export function documentCardsToBusinessCards({
  productCards,
  inquiryCards,
  genericCards,
}: {
  productCards: ProductCard[];
  inquiryCards: InquiryCard[];
  genericCards: GenericCard[];
}): BusinessCard[] {
  return [
    ...productCards.map(productCardToBusinessCard),
    ...inquiryCards.map(inquiryCardToBusinessCard),
    ...genericCards.map(genericCardToBusinessCard),
  ];
}

export function getCardTypeLabel(type: BusinessCard["type"]) {
  return {
    product: "产品卡",
    inquiry: "询盘卡",
    generic: "通用卡",
  }[type];
}

export function getCardStatusLabel(status: NonNullable<BusinessCard["status"]>) {
  return {
    published: "已发布",
    draft: "草稿",
    reviewing: "待审核",
  }[status];
}

function parseGenericPayload(rawJson: string): { title?: string; summary?: string; owner?: string; scenario?: string; tags: string[] } {
  try {
    const value: unknown = JSON.parse(rawJson);
    if (!isPlainObject(value)) return { tags: [] };
    return {
      title: stringField(value.title),
      summary: stringField(value.summary),
      owner: stringField(value.owner),
      scenario: stringField(value.scenario),
      tags: Array.isArray(value.tags) ? value.tags.filter((tag): tag is string => typeof tag === "string" && Boolean(tag.trim())) : [],
    };
  } catch {
    return { tags: [] };
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function stringField(value: unknown) {
  return typeof value === "string" && value.trim() ? value : undefined;
}
