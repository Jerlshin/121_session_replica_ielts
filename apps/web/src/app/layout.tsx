import type { ReactNode } from "react";

export const metadata = {
  title: "IELTS Speaking Platform",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
