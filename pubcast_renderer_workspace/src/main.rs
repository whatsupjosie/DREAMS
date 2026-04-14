use std::sync::Arc;
use winit::{event_loop::EventLoop, window::WindowBuilder};

fn main() {
    let event_loop = EventLoop::new().unwrap();
    let window = Arc::new(WindowBuilder::new().build(&event_loop).unwrap());

    let rt = tokio::runtime::Runtime::new().unwrap();
    rt.block_on(async {
        let mut renderer = pubcast_renderer::renderer::RenderState::new(window.clone()).await;

        event_loop.run(move |event, elwt| {
            match event {
                winit::event::Event::AboutToWait => {
                    let _ = renderer.render(&[]);
                }
                _ => {}
            }
        }).unwrap();
    });
}
