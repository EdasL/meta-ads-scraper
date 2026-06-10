import type { Metadata } from "next";
import { Geist, Geist_Mono, Roboto_Mono } from "next/font/google";
import "./globals.css";
import { Backdrop } from "@/components/chrome";
import { Providers } from "@/components/Providers";

// Geist = body + display, Geist Mono = data, Roboto Mono = technical labels.
const geistSans = Geist({ variable: "--font-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-mono", subsets: ["latin"] });
const robotoMono = Roboto_Mono({ variable: "--font-tech", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Field Desk — Emerging App Finder",
  description: "Find emerging apps that are quietly scaling on Meta ads — caught early, with the evidence.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${robotoMono.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <Backdrop />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
