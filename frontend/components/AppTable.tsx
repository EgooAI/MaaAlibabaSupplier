"use client";

import { Table } from "antd";
import type { TableProps } from "antd";
import { HydrationSafeTable } from "@/components/HydrationSafeTable";

/** 统一空态/水合/布局的业务表格，避免各页散落 components+locale。 */
export function AppTable<T extends object>(props: TableProps<T>) {
  return (
    <Table<T>
      tableLayout="fixed"
      locale={{ emptyText: "暂无数据", ...props.locale }}
      components={{ table: HydrationSafeTable, ...props.components }}
      {...props}
    />
  );
}
