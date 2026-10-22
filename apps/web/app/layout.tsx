import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FinanceMed AI",
  description: "Planejamento financeiro e otimização de agenda para médicos",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
