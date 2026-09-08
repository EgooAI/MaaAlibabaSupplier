export type ID = string;

export interface ApiResponse<T> {
  code: number;
  msg: string;
  data: T;
}
