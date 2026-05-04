FROM python:3.11-slim
WORKDIR /app
RUN echo "Build test successful"
EXPOSE 10000
CMD ["python3", "-c", "import http.server; import socketserver; handler = http.server.SimpleHTTPRequestHandler; httpd = socketserver.TCPServer(('', 10000), handler); httpd.serve_forever()"]
