import type { Metadata, Viewport } from "next";
import { Barlow_Condensed, IBM_Plex_Sans_Arabic } from "next/font/google";
import { notFound } from "next/navigation";

import { Providers } from "@/components/providers";
import { ServiceWorker } from "@/components/service-worker";
import { dirOf, isLocale, locales } from "@/lib/i18n/config";
import { getDictionary } from "@/lib/i18n/dictionaries";

import "../globals.css";

const display = Barlow_Condensed({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["500", "600", "700"],
});

// One family for body text in both languages.
const body = IBM_Plex_Sans_Arabic({
  variable: "--font-body",
  subsets: ["arabic", "latin"],
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "FitCoach PRO",
  description: "Your AI gym coach",
  applicationName: "FitCoach PRO",
  appleWebApp: { capable: true, title: "FitCoach", statusBarStyle: "black-translucent" },
};

export const viewport: Viewport = {
  themeColor: "#0e1116",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export function generateStaticParams() {
  return locales.map((lang) => ({ lang }));
}

export default async function RootLayout({ children, params }: LayoutProps<"/[lang]">) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  return (
    <html
      lang={lang}
      dir={dirOf(lang)}
      className={`dark ${display.variable} ${body.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-background text-foreground">
        <Providers locale={lang} dict={getDictionary(lang)}>
          {children}
        </Providers>
        <ServiceWorker />
      </body>
    </html>
  );
}
