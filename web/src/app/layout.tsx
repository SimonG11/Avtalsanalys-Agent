import type { Metadata } from "next";

import { THEME_SCRIPT } from "@/lib/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: "Avtalsanalys",
  description: "Frågor om Statens inköpscentrals ramavtal, besvarade med källor ur avtalstexten.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // The script sets data-theme before the first paint, so a chosen theme does not flash.
    <html lang="sv" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
