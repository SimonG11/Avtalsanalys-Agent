import type { Metadata } from "next";

import "@copilotkit/react-core/v2/styles.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Avtalsanalys",
  description: "Frågor om Statens inköpscentrals ramavtal, besvarade med källor ur avtalstexten.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="sv">
      <body>{children}</body>
    </html>
  );
}
