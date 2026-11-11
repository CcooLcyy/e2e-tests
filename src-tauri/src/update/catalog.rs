use http::uri::PathAndQuery;
use prost::Message;
use std::time::Duration;
use tonic::{
    client::Grpc,
    codec::ProstCodec,
    transport::{Channel, Endpoint},
    Request, Streaming,
};

pub(super) const PRODUCT: &str = "mskdsp-testbench";
pub(super) const PLATFORM: &str = "windows-x86_64-nsis";
pub(super) const MAX_CHUNK: usize = 256 * 1024;
pub(super) const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(600);

pub(super) struct Catalog(Grpc<Channel>);

impl Catalog {
    pub(super) async fn connect(endpoint: Endpoint) -> Result<Self, String> {
        let channel = endpoint
            .connect_timeout(Duration::from_secs(8))
            .connect()
            .await
            .map_err(|e| format!("连接平台软件目录失败：{e}"))?;
        Ok(Self(Grpc::new(channel)))
    }

    pub(super) async fn list(
        &mut self,
        product: &str,
        channel: &str,
        platform: &str,
    ) -> Result<Vec<Package>, String> {
        let request = ListPackagesRequest {
            product: product.into(),
            channel: channel.into(),
            platform: platform.into(),
        };
        let response: ListPackagesResponse = self
            .query(
                request,
                "/mskdsp.platform.software.v1.SoftwareCatalogService/ListPackages",
            )
            .await?;
        Ok(response.packages)
    }

    pub(super) async fn get(&mut self, package_id: String) -> Result<Package, String> {
        let response: GetPackageResponse = self
            .query(
                GetPackageRequest { package_id },
                "/mskdsp.platform.software.v1.SoftwareCatalogService/GetPackage",
            )
            .await?;
        response.package.ok_or_else(|| "平台未返回软件包".into())
    }

    async fn query<I, O>(&mut self, request: I, path: &'static str) -> Result<O, String>
    where
        I: Message + Send + 'static,
        O: Message + Default + Send + 'static,
    {
        let timeout = Duration::from_secs(15);
        let mut request = Request::new(request);
        request.set_timeout(timeout);
        tokio::time::timeout(timeout, async {
            self.0
                .ready()
                .await
                .map_err(|e| format!("平台软件目录未就绪：{e}"))?;
            self.0
                .unary(
                    request,
                    PathAndQuery::from_static(path),
                    ProstCodec::default(),
                )
                .await
                .map(|response| response.into_inner())
                .map_err(|status| format!("查询平台软件目录失败：{}", status.code()))
        })
        .await
        .map_err(|_| "查询平台软件目录超时")?
    }

    pub(super) async fn download(
        &mut self,
        package_id: String,
    ) -> Result<Streaming<DownloadPackageChunk>, String> {
        let mut request = Request::new(DownloadPackageRequest {
            package_id,
            offset: 0,
        });
        request.set_timeout(DOWNLOAD_TIMEOUT);
        // 额外空间用于分块的偏移、摘要和 protobuf 编码。
        let mut grpc = self.0.clone().max_decoding_message_size(MAX_CHUNK + 1024);
        grpc
            .ready()
            .await
            .map_err(|e| format!("平台下载服务未就绪：{e}"))?;
        grpc
            .server_streaming(
                request,
                PathAndQuery::from_static(
                    "/mskdsp.platform.software.v1.SoftwareCatalogService/DownloadPackage",
                ),
                ProstCodec::default(),
            )
            .await
            .map(|response| response.into_inner())
            .map_err(|status| format!("请求软件包数据流失败：{}", status.code()))
    }
}

// 与 MskDSPPlatformProto/platform/v1/software.proto 保持一致。
// 仅声明更新所用的字段，源码模式无需额外的 proto 仓库或 protoc。
#[derive(Clone, PartialEq, Message)]
struct ListPackagesRequest {
    #[prost(string, tag = "1")]
    product: String,
    #[prost(string, tag = "2")]
    channel: String,
    #[prost(string, tag = "3")]
    platform: String,
}

#[derive(Clone, PartialEq, Message)]
struct ListPackagesResponse {
    #[prost(message, repeated, tag = "1")]
    packages: Vec<Package>,
}

#[derive(Clone, PartialEq, Message)]
struct GetPackageRequest {
    #[prost(string, tag = "1")]
    package_id: String,
}

#[derive(Clone, PartialEq, Message)]
struct GetPackageResponse {
    #[prost(message, optional, tag = "1")]
    package: Option<Package>,
}

#[derive(Clone, PartialEq, Message)]
pub(super) struct Package {
    #[prost(string, tag = "1")]
    pub id: String,
    #[prost(message, optional, tag = "2")]
    pub metadata: Option<PackageMetadata>,
    #[prost(string, repeated, tag = "5")]
    pub channels: Vec<String>,
    #[prost(bool, tag = "6")]
    pub ever_published: bool,
}

#[derive(Clone, PartialEq, Message)]
pub(super) struct PackageMetadata {
    #[prost(string, tag = "1")]
    pub product: String,
    #[prost(string, tag = "2")]
    pub version: String,
    #[prost(string, tag = "3")]
    pub platform: String,
    #[prost(string, tag = "4")]
    pub file_name: String,
    #[prost(uint64, tag = "5")]
    pub size: u64,
    #[prost(string, tag = "6")]
    pub sha256: String,
    #[prost(string, tag = "7")]
    pub signature: String,
    #[prost(string, tag = "8")]
    pub notes: String,
    #[prost(string, tag = "13")]
    pub source_sha: String,
    #[prost(string, tag = "15")]
    pub channel: String,
}

#[derive(Clone, PartialEq, Message)]
struct DownloadPackageRequest {
    #[prost(string, tag = "1")]
    package_id: String,
    #[prost(uint64, tag = "2")]
    offset: u64,
}

#[derive(Clone, PartialEq, Message)]
pub(super) struct DownloadPackageChunk {
    #[prost(uint64, tag = "1")]
    pub offset: u64,
    #[prost(bytes = "vec", tag = "2")]
    pub data: Vec<u8>,
    #[prost(uint64, tag = "3")]
    pub total_size: u64,
    #[prost(string, tag = "4")]
    pub sha256: String,
    #[prost(bool, tag = "5")]
    pub eof: bool,
}
