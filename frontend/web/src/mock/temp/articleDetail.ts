/** 文章详情 Mock 数据 */
export const ArticleDetail: Record<number, string> = {
  452: `
<h2>Node.js + Docker 自动化部署</h2>
<p>本章将介绍 Node.js 使用 Docker、Webhook 自动化部署、蓝绿部署、项目到服务器。</p>
<h3>1、Mac os 安装 Docker 客户端 OrbStack</h3>
<p>我这里使用的是第三方客户端，相比于官方的，较轻量，启动速度快。</p>
<p>OrbStack 是一种快速、轻便且简单的运行 Docker 容器和 Linux 的方法。使用我们的 Docker Desktop 替代方案以光速进行开发。</p>
<h3>2、Dockerfile 编写</h3>
<pre><code class="language-dockerfile">FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install --production
COPY . .
EXPOSE 3000
CMD ["node", "server.js"]
</code></pre>
<h3>3、docker-compose.yml</h3>
<pre><code class="language-yaml">version: '3'
services:
  app:
    build: .
    ports:
      - "3000:3000"
    environment:
      - NODE_ENV=production
    restart: always
</code></pre>
<h3>4、自动化部署流程</h3>
<p>通过 Webhook 实现代码推送后自动触发构建和部署流程，实现 CI/CD。</p>
  `,
  451: `
<h2>HTTP 协议</h2>
<h3>概念</h3>
<p>HTTP（hypertext transport protocol）协议；中文叫超文本传输协议是一种基于 TCP/IP 的应用层通信协议。</p>
<p>这个协议详细规定了浏览器和万维网服务器之间互相通信的规则。</p>
<h3>协议中主要规定了两个方面的内容</h3>
<ul>
  <li><strong>客户端</strong>：用来向服务器发送数据，可以被称之为请求报文</li>
  <li><strong>服务端</strong>：向客户端返回数据，可以被称之为响应报文</li>
</ul>
<h3>请求报文的组成</h3>
<ol>
  <li>请求行</li>
  <li>请求头</li>
  <li>空行</li>
  <li>请求体</li>
</ol>
  `,
  450: `
<h2>MongoDB 数据库基本操作</h2>
<h3>简介</h3>
<p>Mongodb 是什么？MongoDB 是一个基于分布式文件存储的数据库，官方地址 https://www.mongodb.com/</p>
<h3>数据库是什么</h3>
<p>数据库（DataBase）是按照数据结构来组织、存储和管理数据的 应用程序。</p>
<p>数据库的主要作用就是管理数据，对数据进行增（c）、删（d）、改（u）、查（r）。</p>
<h3>数据库管理数据的特点</h3>
<p>相比于纯文件管理数据，数据库管理数据有如下特点：</p>
<ol>
  <li>速度更快</li>
  <li>可扩展性强</li>
  <li>数据持久化</li>
  <li>支持并发操作</li>
</ol>
  `,
};
