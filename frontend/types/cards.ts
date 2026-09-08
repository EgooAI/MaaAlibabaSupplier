import type { ID } from "@/types/common";

export type BusinessCardType = "product" | "inquiry" | "generic";
export type BusinessCardStatus = "published" | "draft" | "reviewing";

export interface ProductCard {
  card_id: string;
  title: string;
  price: string;
  display_price: string;
  product_image: string;
  moq: string;
  moq_unit: string;
  product_id: string;
  product_url: string;
  expired: boolean;
}

export interface InquiryProduct {
  product_name: string;
  product_id: string;
  product_unit_price: string;
  product_moq: string;
  product_unit: string;
  product_image: string;
  discount_price: string;
  product_url: string;
}

export interface InquiryCard {
  inquiry_id: string;
  inquiry_content: string;
  products: InquiryProduct[];
  product_image: string;
  is_seller: boolean;
  attachment_count: string;
}

export interface GenericCard {
  card_type: number;
  card_id: string;
  source_url: string;
  raw_json: string;
}

export interface BusinessCard {
  id: ID;
  title: string;
  type: BusinessCardType;
  status?: BusinessCardStatus;
  summary: string;
  owner?: string;
  updatedAt?: string;
  tags: string[];
  coverTone: string;
  details: Array<{ label: string; value: string }>;
  recommendedScenario?: string;
}
