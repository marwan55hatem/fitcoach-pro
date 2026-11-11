import { ReviewHistory } from "@/components/review/review-history";

// Shown only after the client-side login check, which instant-navigation checks can't see.
export const instant = false;

export default function ReviewHistoryPage() {
  return <ReviewHistory />;
}
