use pubcast_renderer::{CameraManager, FrameBudget, PubCastRenderer, SceneManager};
use winit::{
    event::{Event, WindowEvent},
    event_loop::EventLoop,
    window::WindowBuilder,
};

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt::init();

    let event_loop = EventLoop::new().expect("failed to create event loop");
    let window = WindowBuilder::new()
        .with_title("PubCast Renderer — Gate 2 Test")
        .build(&event_loop)
        .expect("failed to build window");

    let mut renderer = PubCastRenderer::new().expect("failed to create renderer");
    renderer
        .initialize_with_window(&window, FrameBudget::default())
        .await
        .expect("failed to initialize renderer");

    let mut camera_manager = CameraManager::new();
    let scene_manager = SceneManager::new();
    let size = window.inner_size();
    if size.height > 0 {
        camera_manager.set_aspect(size.width as f32 / size.height as f32);
    }

    event_loop.run(move |event, elwt| {
        match event {
            Event::WindowEvent { event, .. } => match event {
                WindowEvent::CloseRequested => elwt.exit(),
                WindowEvent::Resized(size) => {
                    renderer.resize(size);
                    if size.height > 0 {
                        camera_manager.set_aspect(size.width as f32 / size.height as f32);
                    }
                }
                WindowEvent::RedrawRequested => {
                    let rt = tokio::runtime::Handle::current();
                    match rt.block_on(renderer.render_frame(&camera_manager, &scene_manager)) {
                        Ok(frame) => {
                            if let Some(err) = frame.error {
                                eprintln!("frame warning: {err}");
                            }
                        }
                        Err(err) => {
                            eprintln!("render error: {err}");
                            elwt.exit();
                        }
                    }
                }
                _ => {}
            },
            Event::AboutToWait => {
                window.request_redraw();
            }
            _ => {}
        }
    }).expect("event loop failed");
}
