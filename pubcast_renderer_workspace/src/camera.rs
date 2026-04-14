use cgmath::{perspective, Deg, InnerSpace, Matrix4, Point3, Vector3};

pub struct Camera {
    pub position: Vector3<f32>,
    pub target: Vector3<f32>,
    pub up: Vector3<f32>,
    pub fov: f32,
    pub aspect: f32,
    pub near: f32,
    pub far: f32,
}

impl Camera {
    pub fn new() -> Self {
        Self {
            position: Vector3::new(0.0, 2.0, 5.0),
            target: Vector3::new(0.0, 0.0, 0.0),
            up: Vector3::new(0.0, 1.0, 0.0),
            fov: 45.0,
            aspect: 16.0 / 9.0,
            near: 0.1,
            far: 1000.0,
        }
    }

    pub fn build_view_projection_matrix(&self) -> Matrix4<f32> {
        let eye = Point3::new(self.position.x, self.position.y, self.position.z);
        let center = Point3::new(self.target.x, self.target.y, self.target.z);
        let up = if self.up.magnitude2() == 0.0 { Vector3::unit_y() } else { self.up.normalize() };
        let view = Matrix4::look_at_rh(eye, center, up);
        let proj = perspective(Deg(self.fov), self.aspect, self.near, self.far);
        proj * view
    }
}

pub struct CameraManager {
    camera: Camera,
}

impl CameraManager {
    pub fn new() -> Self {
        Self { camera: Camera::new() }
    }

    pub fn get_active_camera(&self) -> Option<&Camera> {
        Some(&self.camera)
    }

    pub fn set_position(&mut self, x: f32, y: f32, z: f32) {
        self.camera.position = Vector3::new(x, y, z);
    }

    pub fn set_target(&mut self, x: f32, y: f32, z: f32) {
        self.camera.target = Vector3::new(x, y, z);
    }

    pub fn set_aspect(&mut self, aspect: f32) {
        if aspect > 0.0 {
            self.camera.aspect = aspect;
        }
    }
}
