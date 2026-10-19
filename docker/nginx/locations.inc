# Shared by http.conf and tls.conf.
root /usr/share/nginx/html;
index index.html;

client_max_body_size 60m;          # keep >= UPLOAD_MAX_MB
server_tokens off;

include /etc/nginx/contexthub/headers.inc;

location /api/ {
    proxy_pass http://api:8000/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Request-ID $request_id;
    # Server-Sent Events: stream tokens as they come.
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
}

# Prometheus metrics stay internal.
location = /api/metrics { return 404; }

location /assets/ {
    expires 30d;
    include /etc/nginx/contexthub/headers.inc;
    add_header Cache-Control "public, immutable";
    try_files $uri =404;
}

location / {
    include /etc/nginx/contexthub/headers.inc;
    add_header Cache-Control "no-cache";
    try_files $uri /index.html;
}
