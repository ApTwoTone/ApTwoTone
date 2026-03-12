import { authkitMiddleware } from "@workos-inc/authkit-nextjs";

export default authkitMiddleware({
  // Routes that require authentication
  middlewareAuth: {
    enabled: true,
    unauthenticatedPaths: ["/", "/pricing", "/about", "/api/health"],
  },
});

export const config = {
  matcher: [
    // Match all routes except static files and Next.js internals
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
