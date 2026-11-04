import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "QA Intelligence AI · Centro de calidad",
  description: "Plataforma local de ingeniería de calidad de software",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="es"><body>{children}</body></html>;
}
