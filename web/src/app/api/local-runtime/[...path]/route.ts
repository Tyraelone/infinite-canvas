import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 1800;

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
    if (process.env.CANVAS_LOCAL_MODE !== "1") return new Response(null, { status: 404 });
    const publicUrl = new URL(`http://${request.headers.get("host") || "invalid"}`);
    if (!["127.0.0.1", "localhost", "[::1]"].includes(publicUrl.hostname)) return new Response(null, { status: 403 });
    const origin = request.headers.get("origin");
    if ((origin && origin !== publicUrl.origin) || request.headers.get("sec-fetch-site") === "cross-site") return new Response(null, { status: 403 });
    const { path } = await context.params;
    if (path.join("/") === "profile") {
        return Response.json({ enabled: true, baseUrl: `${publicUrl.origin}/api/local-runtime/v1` });
    }
    const workspace = path[0] === "workspace";
    const target = workspace
        ? `${process.env.API_BASE_URL || "http://127.0.0.1:8081"}/api/local-workspace/${path.slice(1).map(encodeURIComponent).join("/")}${request.nextUrl.search}`
        : `${process.env.CANVAS_LOCAL_URL || "http://127.0.0.1:18291"}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
    const headers = new Headers();
    for (const name of ["content-type", "range", "if-range", "x-workspace-client", "x-workspace-revision"]) {
        const value = request.headers.get(name);
        if (value) headers.set(name, value);
    }
    headers.set("authorization", `Bearer ${process.env.CANVAS_LOCAL_TOKEN || ""}`);
    const hasBody = !["GET", "HEAD"].includes(request.method);
    try {
        const response = await fetch(target, {
            method: request.method, headers, body: hasBody ? request.body : undefined,
            duplex: hasBody ? "half" : undefined, signal: request.signal,
        } as RequestInit & { duplex?: "half" });
        if (workspace && path[1] === "state") console.info(JSON.stringify({ service: "workspace-state", method: request.method,
            slot: path[2], client: request.headers.get("x-workspace-client") || "local-api", revision: request.headers.get("x-workspace-revision"), status: response.status }));
        const outputHeaders = new Headers(response.headers);
        for (const name of ["content-encoding", "transfer-encoding", "connection"]) outputHeaders.delete(name);
        if (!workspace || response.headers.has("content-encoding")) outputHeaders.delete("content-length");
        return new Response(response.body, { status: response.status, headers: outputHeaders });
    } catch {
        return Response.json({ error: { message: "本机 AI 接口未启动，请运行“启动无限画布.cmd”" } }, { status: 502 });
    }
}

export const GET = proxy;
export const HEAD = proxy;
export const POST = proxy;
export const PUT = proxy;
export const DELETE = proxy;
