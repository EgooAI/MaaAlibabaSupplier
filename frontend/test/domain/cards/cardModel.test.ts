import { describe, expect, it } from "vitest";
import { genericCardToBusinessCard, inquiryCardToBusinessCard, productCardToBusinessCard } from "@/domain/cards/cardModel";
import type { GenericCard } from "@/types/cards";

const card = (rawJson: string): GenericCard => ({
  card_id: "generic-1",
  card_type: 7,
  source_url: "https://example.com/card",
  raw_json: rawJson,
});

describe("card model", () => {
  it("does not invent product card status, owner, timestamps or scenarios", () => {
    const businessCard = productCardToBusinessCard({
      card_id: "product-1",
      title: "Product",
      price: "USD 10",
      display_price: "USD 9",
      product_image: "",
      moq: "100",
      moq_unit: "件",
      product_id: "p-1",
      product_url: "https://example.com/product",
      expired: false,
    });

    expect(businessCard.status).toBeUndefined();
    expect(businessCard.owner).toBeUndefined();
    expect(businessCard.updatedAt).toBeUndefined();
    expect(businessCard.recommendedScenario).toBeUndefined();
  });

  it("does not invent inquiry card status, owner, timestamps or scenarios", () => {
    const businessCard = inquiryCardToBusinessCard({
      inquiry_id: "inquiry-1",
      inquiry_content: "Need quote",
      products: [],
      product_image: "",
      is_seller: true,
      attachment_count: "0",
    });

    expect(businessCard.status).toBeUndefined();
    expect(businessCard.owner).toBeUndefined();
    expect(businessCard.updatedAt).toBeUndefined();
    expect(businessCard.recommendedScenario).toBeUndefined();
  });

  it("uses valid generic payload fields", () => {
    const businessCard = genericCardToBusinessCard(card(JSON.stringify({ title: "标题", summary: "摘要", owner: "负责人", scenario: "报价场景", tags: ["报价", "  ", 1] })));

    expect(businessCard).toMatchObject({
      id: "generic-1",
      title: "标题",
      summary: "摘要",
      owner: "负责人",
      recommendedScenario: "报价场景",
      tags: ["报价"],
    });
  });

  it("ignores non-object and malformed payloads", () => {
    expect(genericCardToBusinessCard(card("null"))).toMatchObject({ title: "通用卡片 generic-1", tags: ["通用卡", "类型 7"] });
    expect(genericCardToBusinessCard(card("[]"))).toMatchObject({ title: "通用卡片 generic-1", tags: ["通用卡", "类型 7"] });
    expect(genericCardToBusinessCard(card("not json"))).toMatchObject({ title: "通用卡片 generic-1", tags: ["通用卡", "类型 7"] });
  });

  it("ignores fields with invalid runtime types", () => {
    const businessCard = genericCardToBusinessCard(card(JSON.stringify({ title: 123, summary: null, owner: [], scenario: {}, tags: [1, false, "有效"] })));

    expect(businessCard).toMatchObject({
      title: "通用卡片 generic-1",
      summary: "通用运营卡片",
      tags: ["有效"],
    });
    expect(businessCard.owner).toBeUndefined();
    expect(businessCard.recommendedScenario).toBeUndefined();
    expect(businessCard.status).toBeUndefined();
    expect(businessCard.updatedAt).toBeUndefined();
  });
});
