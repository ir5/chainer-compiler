#include <gtest/gtest.h>

#include <configs/backend_config.h>

namespace chainer_compiler {
namespace {

TEST(BackendConfigTest, ChxVM) {
    std::unique_ptr<BackendConfig> config = BackendConfig::FromName("chxvm");
    EXPECT_EQ(1, config->GetSimplifiers().count("ReplaceLess"));
    EXPECT_EQ(0, config->GetSimplifiers().count("NOT FOUND"));
}

}  // namespace
}  // namespace chainer_compiler
