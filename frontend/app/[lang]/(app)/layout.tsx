import { AppShell } from "@/components/app-shell";

export default function AppLayout({ children }: LayoutProps<"/[lang]">) {
  return <AppShell>{children}</AppShell>;
}
