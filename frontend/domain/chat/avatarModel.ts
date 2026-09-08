export const sellerAvatarUrl = "https://i.pravatar.cc/80?img=12";
export const fallbackAvatarUrl = "/globe.svg";

export function conversationAvatarUrl(id: string) {
  const seed = Array.from(id).reduce((total, character) => total + character.charCodeAt(0), 0);
  return `https://i.pravatar.cc/80?img=${(seed % 70) + 1}`;
}
