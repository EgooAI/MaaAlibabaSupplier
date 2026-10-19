import { MessageOutlined, ShoppingOutlined } from "@ant-design/icons";
import { ProductQueryResult, ReplyCustomerResult } from "./ToolCallCard";
import { registerToolRenderer } from "./toolRenderers";

/** Built-in renderers; loaded once when the timeline module is imported. */
registerToolRenderer("reply_customer", {
  icon: <MessageOutlined />,
  summarize: (args) => {
    const reply = typeof args.reply === "string" ? args.reply.replace(/\s+/g, " ").trim() : "";
    if (!reply) return "";
    return reply.length > 60 ? `${reply.slice(0, 57)}...` : reply;
  },
  render: ({ call, simulated, intent }) => <ReplyCustomerResult call={call} simulated={simulated} intent={intent} />,
});

registerToolRenderer("query_product", {
  icon: <ShoppingOutlined />,
  summarize: (args) => {
    const productId = typeof args.product_id === "string" ? args.product_id.trim() : "";
    const keywords = typeof args.keywords === "string" ? args.keywords.trim() : "";
    return [productId, keywords].filter(Boolean).join(" · ");
  },
  render: ({ call }) => <ProductQueryResult call={call} />,
});

export {};
