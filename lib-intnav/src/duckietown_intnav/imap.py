#!/usr/bin/env python
###############################################################################
# Duckietown - Project intnav ETH
# Author: Simon Schaefer
# Intersection map representation core class - Contains map array, world-map-
# transformations, status and visualization methods. 
# Each grid cell is assigned to a value containing state information: 
# 0 -> environment
# 1 -> street
# 2 -> white line
# 3 -> red line
# 4 -> yellow line
###############################################################################
__all__ = [
    'IMap',
]

import os
import numpy as np
from PIL import Image, ImageDraw
import sys
import warnings

# Define constant intersection parameters. 
IMAP_DIS_WHITE = 0.05                # White line width [m]. 
IMAP_DIS_LANE = 0.22                 # One lane width (between yellow & white) [m]. 
IMAP_DIS_YELLOW = 0.025              # Half width of yellow line [m]. 
IMAP_DIS_YEL_DIS = 0.03              # Distance between yellow line segments [m]. 

IMAP_COLORS = {'white_line': (255,255,255), 
               'lane': (130,130,130), 
               'red_line': (0,0,255),
               'yellow_line': (0,255,255)}

class IMap(object):

    # Structure-based encodings.
    v_env = 0
    v_str = 10
    v_whi = 50
    v_red = 99
    v_yel = 100
    # Additional visualisation encodings (only in pre_image !).
    v_coord_system = 20
    v_trajectory = 21
    v_path = 22

    def __init__(self, itype, resolution=0.005, 
                vis_point_rad=0.001,vis_car_width=0.07, vis_car_height=0.05): 
        ''' iMap class initialization: Build map of given type and given 
        resolution as internal numpy array. 
        @param[in]  itype       intersection type ("4","3LR","3SL","3SR"). 
                                with L = left, R = right, S = straight. '''  
        # Set internal iMap parameters. Map width is exactly three times the
        # width of a street (= 6 times the width of half street width s).
        # According to the duckietown norms the red line height is exactly 
        # equal to the width of the white line. 
        self.resolution = resolution
        s = int((IMAP_DIS_WHITE
            + IMAP_DIS_YELLOW/2
            + IMAP_DIS_LANE)/resolution) 
        self.data, self.width, self.height = IMap.create_data_from_type(itype, 
                                                                self.resolution)
        self.data = np.flip(self.data, 1)
        # Build colored map representation. 
        self.data_colored = np.zeros((self.width, self.height, 3), dtype=np.uint8)
        color_dict = {IMap.v_str: 'lane', 
                      IMap.v_whi: 'white_line', 
                      IMap.v_red: 'red_line', 
                      IMap.v_yel: 'yellow_line'}
        for i in range(self.width): 
            for j in range(self.height): 
                cell_type = self.data[i][j]
                if cell_type == IMap.v_env:
                    continue
                classifier = color_dict[cell_type]
                colors = IMAP_COLORS[classifier]
                self.data_colored[i,j,0] = colors[0]
                self.data_colored[i,j,1] = colors[1]
                self.data_colored[i,j,2] = colors[2]
        # Set origin position to midth of lower street. 
        self._origin_p = (3*s, 2*s)
        # Build special points dictionary. 
        self.special_points = {}
        self.special_points['start'] = self.transform_pixel_world(2.5*s,1.5*s)
        self.special_points['goal_left'] = self.transform_pixel_world(4.5*s,2.5*s)
        self.special_points['goal_straight'] = self.transform_pixel_world(2.5*s,4.5*s)
        self.special_points['goal_right'] = self.transform_pixel_world(1.5*s,1.5*s)
        # Prerender image for visualization, this image can be overlayed 
        # without changing the imap itself (e.g. by the trajectory). 
        # Change trajectory as well in order to not redraw the visualization
        # if updated trajectory happens to be the same already visualized
        # trajectory. 
        self._pre_image = None
        self._vis_point_rad = int(vis_point_rad/self.resolution)
        self._vis_car_w = int(vis_car_width/self.resolution) 
        self._vis_car_h = int(vis_car_height/self.resolution) 
        self._pre_trajectory = []
        self._pre_path = []
        self.visualize_init()

    @staticmethod
    def create_data_from_type(itype, resolution): 
        s = int((IMAP_DIS_WHITE
            + IMAP_DIS_YELLOW/2
            + IMAP_DIS_LANE)/resolution)
        wl = int(IMAP_DIS_WHITE/resolution)
        yl = int(IMAP_DIS_YELLOW/2/resolution)
        rh = int(IMAP_DIS_WHITE/resolution)
        yd = int(IMAP_DIS_YEL_DIS/resolution) 
        width, height = 6*s, 6*s
        data = IMap.v_env*np.ones((width, height), dtype=np.uint8)
        # Loading map type to internal array, by exploiting map's symmetry. 
        # Basically every map type can be represented by mirroring its smallest 
        # symmetry element which is a street. 
        street_img = IMap.v_str*np.ones((2*s,2*s), dtype=np.uint8)
        street_img[s-yl:s+yl,:] = IMap.v_yel
        street_img[0:wl,:] = IMap.v_whi
        street_img[2*s-wl:2*s,:] = IMap.v_whi
        red_line_img = IMap.v_red*np.ones((s+yl,rh), dtype=np.uint8)
        red_line_img[0:wl,:] = IMap.v_whi
        red_line_img[s-yl:s+yl,:] = IMap.v_yel
        yellow_line_segements = 2*s/yd
        for k in range(yellow_line_segements):
            if k % 2 == 0: 
                street_img[s-yl:s+yl,2*s-(k+1)*yd:2*s-k*yd] = IMap.v_str
        # Build up high level structure from basic structure. 
        if itype == "4":
            # Build basic structure. 
            data[2*s:4*s,2*s:4*s] = IMap.v_str
            data[2*s:4*s,0:2*s] = street_img
            data[2*s:4*s,4*s:6*s] = np.flip(street_img, 1)
            data[0:2*s,2*s:4*s] = np.transpose(street_img)
            data[4*s:6*s,2*s:4*s] = np.flip(np.transpose(street_img), 0)
            # Reconstruct white line edges and set red lines. 
            data[2*s:3*s+yl,2*s:2*s+rh] = red_line_img
            data[3*s-yl:4*s,4*s-rh:4*s] = np.flip(red_line_img, 0)
            data[2*s:2*s+rh,3*s-yl:4*s] = np.flip(np.transpose(red_line_img), 1)
            data[4*s-rh:4*s,2*s:3*s+yl] = np.transpose(red_line_img)
        elif itype == "3LR": 
            # Build basic structure. 
            data[2*s:4*s,2*s:4*s] = IMap.v_str
            data[2*s:4*s,0:2*s] = street_img
            data[0:2*s,2*s:4*s] = np.transpose(street_img)
            data[4*s:6*s,2*s:4*s] = np.flip(np.transpose(street_img), 0)
             # Reconstruct white line edges and set red lines. s
            data[2*s:3*s+yl,2*s:2*s+rh] = red_line_img
            data[2*s:2*s+rh,3*s-yl:4*s] = np.flip(np.transpose(red_line_img), 1)
            data[4*s-rh:4*s,2*s:3*s+yl] = np.transpose(red_line_img)
            data[2*s:4*s,4*s-wl:4*s] = IMap.v_whi
        elif itype == "3SL": 
            # Build basic structure. 
            data[2*s:4*s,2*s:4*s] = IMap.v_str
            data[2*s:4*s,0:2*s] = street_img
            data[2*s:4*s,4*s:6*s] = np.flip(street_img, 1)
            data[4*s:6*s,2*s:4*s] = np.flip(np.transpose(street_img), 0)
            # Reconstruct white line edges and set red lines. s
            data[2*s:3*s+yl,2*s:2*s+rh] = red_line_img
            data[3*s-yl:4*s,4*s-rh:4*s] = np.flip(red_line_img, 0)
            data[4*s-rh:4*s,2*s:3*s+yl] = np.transpose(red_line_img)
            data[2*s:2*s+wl,2*s:4*s] = IMap.v_whi   
        elif itype == "3SR": 
            # Build basic structure. 
            data[2*s:4*s,2*s:4*s] = IMap.v_str
            data[2*s:4*s,0:2*s] = street_img
            data[2*s:4*s,4*s:6*s] = np.flip(street_img, 1)
            data[0:2*s,2*s:4*s] = np.transpose(street_img)
            # Reconstruct white line edges and set red lines. s
            data[2*s:3*s+yl,2*s:2*s+rh] = red_line_img
            data[3*s-yl:4*s,4*s-rh:4*s] = np.flip(red_line_img, 0)
            data[2*s:2*s+rh,3*s-yl:4*s] = np.flip(np.transpose(red_line_img), 1)
            data[4*s-wl:4*s,2*s:4*s] = IMap.v_whi            
        else:
            raise ValueError("Unknown intersection type %s !" % itype)
        return data, width, height

    def in_map_pixel(self, u, v): 
        ''' Check whether pixel coordinate is in map. '''
        return 0 <= u < self.width and 0 <= v < self.height

    def in_map_world(self, x, y): 
        ''' Check whether world coordinate is in map. '''
        u, v = self.transform_world_pixel(x,y)
        return self.in_map_pixel(u,v)

    def transform_pixel_world(self, u, v): 
        ''' Transform pixel coordinates to world coordinates. 
        Attention: Due to timing considerations no check is done 
        here whether the stated coordinates are within the imap !
        @param[in]  u       pixel width coordinate, int.  
        @param[in]  v       pixel height coordinate, int.
        @param[out] x       world coordiantes [m]. 
        @param[out] y       world coordiantes [m]. '''
        x = (v - self._origin_p[1])*self.resolution
        y = (u - self._origin_p[0])*self.resolution
        return float(x), float(y)

    def transform_world_pixel(self, x, y): 
        ''' Transform world coordinates to pixel coordinates. 
        Attention: Due to timing considerations no check is done 
        here whether the stated coordinates are within the imap !
        @param[in] x       world coordiantes [m], float. 
        @param[in] y       world coordiantes [m], float. 
        @param[out]  u       pixel width coordinate. 
        @param[out]  v       pixel height coordinate. '''
        u = y/self.resolution + self._origin_p[0]
        v = x/self.resolution + self._origin_p[1]
        return int(u), int(v)

    def visualize_add_coord_system(self): 
        ''' Adding coordinate system to visualization. '''
        x,y = self._origin_p
        # Draw x-axis. 
        u,v = self.transform_world_pixel(0.01, 0.05)
        self._pre_image[x:u,y:v] = IMap.v_coord_system
        # Draw y-axis. 
        u,v = self.transform_world_pixel(0.05, 0.01)
        self._pre_image[x:u,y:v] = IMap.v_coord_system
        return True

    def visualize_init(self): 
        ''' (Re)Initialize visualization by appending basic
        structure and basic elements such as coordiate system. '''
        self._pre_image = np.copy(self.data)
        self.visualize_add_coord_system()

    def visualize_add_path(self, path): 
        ''' Adding path (target-trajectory) to visualization. 
        @param[in]  path            trajectory world coordinates in
                                    format [[x1,y1],[x2,y2],...]. '''
        # Check whether redrawing is necessary, or already visualized
        # path the same as new path. 
        succeded = True
        if len(self._pre_path) == len(path): 
            diff = 0.0
            for k in range(len(path)): 
                pp, pt = self._pre_path[k], path[k]
                diff += abs(pp[0]-pt[0]) + abs(pp[1]-pt[1])
            if diff < 1.0:
                return succeded
        # Reinitialize visualization in case of "old" trajectories. 
        self.visualize_init()
        # Add every trajectory point to visualization. 
        r = self._vis_point_rad
        for point in path: 
            x,y = point
            u,v = self.transform_world_pixel(x,y)
            if self.in_map_pixel(u,v): 
                self._pre_image[u-r:u+r,v-r:v+r] = IMap.v_path
            else: 
                warnings.warn("Path point (%f,%f) not in iMap !" % (x,y), Warning)
                succeded = False
        # Update pre_rendered trajectory. 
        self._pre_path = path
        return succeded                                    

    def visualize_add_trajectory(self, trajectory): 
        ''' Adding trajectory data to visualization. 
        @param[in]  trajectory      trajectory world coordinates in
                                    format [[x1,y1],[x2,y2],...]. '''
        # Check whether redrawing is necessary, or already visualized
        # trajectory the same as new trajectory. 
        succeded = True
        if len(self._pre_trajectory) == len(trajectory): 
            diff = 0.0
            for k in range(len(trajectory)): 
                pp, pt = self._pre_trajectory[k], trajectory[k]
                diff += abs(pp[0]-pt[0]) + abs(pp[1]-pt[1])
            if diff < 1.0:
                return succeded
        # Reinitialize visualization in case of "old" trajectories. 
        self.visualize_init()
        # Add every trajectory point to visualization. 
        r = self._vis_point_rad
        for point in trajectory: 
            x,y = point
            u,v = self.transform_world_pixel(x,y)
            if self.in_map_pixel(u,v): 
                self._pre_image[u-r:u+r,v-r:v+r] = IMap.v_trajectory
            else: 
                warnings.warn("Trajectory point (%f,%f) not in iMap !" % (x,y), Warning)
                succeded = False
        # Update pre_rendered trajectory. 
        self._pre_trajectory = trajectory
        return succeded

    def visualize_add_robot(self, image, pose): 
        ''' Add rectangle as visualization of robot to image (not pre-
        rendered image !) using PIL library. 
        @param[in]  image           image to add robot to.
        @param[in]  pose            robot's pose description in world 
                                    coordinates [m,rad] (x,y,thetha). '''
        def get_rect(x, y, width, height, angle):
            rect = np.array([(0, 0), (width, 0), (width, height), (0, height)])
            R = np.array([[np.cos(theta), -np.sin(theta)],
                        [np.sin(theta), np.cos(theta)]])
            offset = np.array([x, y])
            transformed_rect = np.dot(rect, R) + offset
            return transformed_rect
            
        # Check pose information structure.
        if not len(pose) == 3: 
            return image, False
        x,y,theta = pose
        theta = -theta # due to different coordinate conventions in PIL.
        uc,vc = self.transform_world_pixel(x,y)
        if not self.in_map_pixel(uc,vc): 
            return image, False
        # Draw rectangle on image using the ImageDraw.Draw function from 
        # PIL standard library. To do so convert pose to rectangle with 
        # uc,vc as center coordinates and draw polygon on PIL image. 
        # Afterwards backtransform PIL image to numpy array. 
        w = self._vis_car_w
        h = self._vis_car_h
        img = Image.fromarray(image)
        # Draw a rotated rectangle on the image.
        draw = ImageDraw.Draw(img)
        rect = get_rect(x=vc-w/2, y=uc-h/2, width=w, height=h, angle=theta)
        draw.polygon([tuple(p) for p in rect], fill=128)
        # Convert the Image data to a numpy array.
        return np.asarray(img), True

    def visualize(self, pose=None): 
        ''' Return rendered image as numpy array in order to visualize
        it (by matplotlib or by sensor_msgs::Image conversion). For efficiency
        reasons a prerendered image is used that is merely changed when a
        new trajectory or a new pose to publish are added to the
        visualisation, not at every call of the visualize function. '''
        if pose is None: 
            # Transpose image as numpy convention is different than "norm". 
            return self._pre_image
        # When additional feature should be drawn, the prerendered image 
        # should not be changed, as it would have to be initialized again.
        # Therefore, new image is created. 
        image = np.copy(self._pre_image)
        # Add robots pose if necessary. 
        if len(pose) > 0: 
            image, _ = self.visualize_add_robot(image, pose)
        # Transpose image as numpy convention is different than "norm". 
        return image

    @staticmethod
    def imap_types(): 
        ''' Returns implemented imap types. '''
        return ["4","3LR","3SL","3SR"]
