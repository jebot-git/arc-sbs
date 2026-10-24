// SPDX-License-Identifier: MIT
#define ARC_SBS_TESTING
#include "../main.cpp"
#include <gio/gio.h>
#include <stdexcept>
void require(bool yes, const char *message) {
  if (!yes)
    throw std::runtime_error(message);
}
template <class F> void wait(F condition) {
  auto end = std::chrono::steady_clock::now() + std::chrono::seconds(8);
  while (!condition() && std::chrono::steady_clock::now() < end)
    Fl::wait(.025);
  require(condition(), "UI wait timed out");
}
int main(int argc, char **argv) try {
  require(argc == 2, "Expected test directory");
  dx::init();
  Converter app;
  dx::style(&app);
  app.show();
  for (int i = 0; i < 20; ++i)
    Fl::wait(.025);
  require(app.layoutFits(), "Initial native layout fits");
  require(!app.menuBar().universal() && app.formTop() == 25,
          "Local menu remains without a host");
  auto bus = g_bus_get_sync(G_BUS_TYPE_SESSION, nullptr, nullptr);
  require(bus, "Private DBus session");
  auto request = g_dbus_connection_call_sync(
      bus, "org.freedesktop.DBus", "/org/freedesktop/DBus",
      "org.freedesktop.DBus", "RequestName",
      g_variant_new("(su)", "com.canonical.AppMenu.Registrar", 0), nullptr,
      G_DBUS_CALL_FLAGS_NONE, 1000, nullptr, nullptr);
  require(request, "Acquire menu host");
  g_variant_unref(request);
  wait([&] { return app.menuBar().universal(); });
  require(app.menuBar().h() == 0 && app.formTop() == 0,
          "Universal menu reclaims its strip");
  fs::path directory = argv[1];
  auto source = directory / "vídeo 日本語 with spaces.mp4";
  std::ofstream(source) << "input";
  app.setSource(source.string());
  require(app.inputPath() == source.string(), "Unicode input path preserved");
  require(app.outputPath() ==
              (directory / "vídeo 日本語 with spaces-sbs.mp4").string(),
          "Output path derives from selected input");
  Fl::focus(nullptr);
  Fl::e_keysym = FL_Enter;
  Fl::e_state = FL_CTRL;
  Fl::handle(FL_SHORTCUT, &app);
  Fl::e_state = 0;
  wait([&] { return app.busy(); });
  wait([&] { return !app.busy(); });
  wait([&] { return app.canPlay(); });
  require(fs::is_regular_file(app.outputPath()),
          "Background worker completion enables playback");
  app.invoke("Play output");
  wait([&] { return fs::exists(directory / "player.json"); });
  Json args;
  std::ifstream(directory / "player.json") >> args;
  require(
      args == Json::array({"--xr-output=auto", "--sbs-layout=full-sbs",
                           "--swap-eyes", "--", app.outputPath()}),
      "mpvr receives exact stereo layout, eye order and Unicode output path");
  auto config = dx::config();
  fs::create_directories(config.parent_path());
  std::ofstream(config) << Json{
      {"font", "Unifont / Sans-Serif"}, {"font_size", 16}, {"font_scale", 2}};
  auto old = dx::font_revision;
  wait([&] { return dx::font_revision != old; });
  for (int i = 0; i < 8; ++i)
    Fl::wait(.05);
  app.size(600, 500);
  require(app.layoutFits(),
          "Live 2x bitmap appearance fits a small scrollable form");
  auto release = g_dbus_connection_call_sync(
      bus, "org.freedesktop.DBus", "/org/freedesktop/DBus",
      "org.freedesktop.DBus", "ReleaseName",
      g_variant_new("(s)", "com.canonical.AppMenu.Registrar"), nullptr,
      G_DBUS_CALL_FLAGS_NONE, 1000, nullptr, nullptr);
  require(release, "Release menu host");
  g_variant_unref(release);
  wait([&] { return !app.menuBar().universal(); });
  require(app.formTop() == 25 && app.menuBar().h() == 25,
          "Local menu restores on host loss");
  require(app.capture((directory / "frontend.ppm").c_str()), "GL frontend capture");
  app.hide();
  g_object_unref(bus);
  std::cout << "Native UI: host gain/loss, no menu strip, hidden-menu "
               "shortcut, Unicode paths, async worker, exact mpvr stereo args "
               "and live 2x appearance passed\n";
  return 0;
} catch (const std::exception &e) {
  std::cerr << e.what() << '\n';
  return 1;
}
