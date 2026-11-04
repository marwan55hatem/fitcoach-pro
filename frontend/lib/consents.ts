"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import type { Consents, ConsentType } from "@/lib/api/types";

export const CONSENTS_KEY = ["consents"] as const;

/** Which consents are granted right now, as a simple map. */
export function useConsents() {
  return useQuery({
    queryKey: CONSENTS_KEY,
    queryFn: async () => {
      const { consents } = await api<Consents>("/consents");
      return Object.fromEntries(consents.map((c) => [c.type, c.granted])) as Record<ConsentType, boolean>;
    },
  });
}
