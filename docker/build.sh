#!/bin/bash
# N.E.K.O. Docker 镜像构建脚本

set -e

IMAGE_NAME="ghcr.io/project-n-e-k-o/n.e.k.o"
IMAGE_TAG="debian-x86_64-v0.5.3"
DOCKERFILE="Dockerfile"

echo "🐱 Building N.E.K.O. Docker image..."

# 检查 Docker 是否可用
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed or not in PATH"
    exit 1
fi

# 构建镜像
echo "🔨 Building image: ${IMAGE_NAME}:${IMAGE_TAG}"
docker build -t ${IMAGE_NAME}:${IMAGE_TAG} -f ${DOCKERFILE} .

# 检查构建是否成功
if [ $? -eq 0 ]; then
    echo "✅ Image built successfully!"
    echo "📦 Image: ${IMAGE_NAME}:${IMAGE_TAG}"
    echo ""
    echo "🚀 To run the container:"
    echo "   docker run -d -p 48911:48911 -v neko-data:/data ${IMAGE_NAME}:${IMAGE_TAG}"
    echo ""
    echo "🔧 Environment variables:"
    echo "   NEKO_CORE_API_KEY=your_api_key"
    echo "   NEKO_CORE_API=qwen"
    echo "   NEKO_MAIN_SERVER_PORT=48911"
else
    echo "❌ Image build failed"
    exit 1
fi
