import { SettingsView } from "@/components/settings-view";

// Shown only after the client-side login check, which instant-navigation checks can't see.
export const instant = false;

export default function SettingsPage() {
  return <SettingsView />;
}
