"use client";

import { Drawer, Typography } from "antd";
import { BusinessCardView } from "@/components/BusinessCardView";
import type { BusinessCard } from "@/types/cards";

export function CardDetailDrawer({ card, open, onClose }: { card?: BusinessCard; open: boolean; onClose: () => void }) {
  return (
    <Drawer title="业务卡片详情" size={520} open={open} onClose={onClose} destroyOnHidden>
      {card ? (
        <div className="space-y-4">
          <BusinessCardView card={card} />
          {card.recommendedScenario ? (
            <div>
              <Typography.Title level={5}>推荐使用场景</Typography.Title>
              <Typography.Paragraph>{card.recommendedScenario}</Typography.Paragraph>
            </div>
          ) : null}
        </div>
      ) : null}
    </Drawer>
  );
}
