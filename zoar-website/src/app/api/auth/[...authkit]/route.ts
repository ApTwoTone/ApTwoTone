import { handleAuth } from "@workos-inc/authkit-nextjs";
import { NextRequest } from "next/server";

const handler = handleAuth();

export async function GET(request: NextRequest) {
  return handler(request);
}
