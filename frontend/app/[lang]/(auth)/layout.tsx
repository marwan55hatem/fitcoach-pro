import { Brand } from "@/components/brand";
import { LanguageSwitch } from "@/components/language-switch";

export default function AuthLayout({ children }: LayoutProps<"/[lang]">) {
  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-md flex-col px-5 pb-10 pt-[max(1rem,env(safe-area-inset-top))]">
      <header className="flex items-center justify-between py-3">
        <Brand />
        <LanguageSwitch />
      </header>
      <main className="flex flex-1 flex-col justify-center py-8">{children}</main>
    </div>
  );
}
